from __future__ import print_function
from __future__ import division
from __future__ import absolute_import
from future import standard_library
standard_library.install_aliases()
from builtins import *

import h5py
import numpy as np
import numpy.lib.recfunctions as nprf
import sys

from Bio import SeqIO

from untangled import bio, fast5, maths

# NB: qualified imports here due to a name clash
import sloika.decode
import sloika.util


TRIM_OPEN_PORE_LOCAL_VAR_METHODS = frozenset(['mad', 'std'])

DEFAULT_NORMALISATION = 'per-read'

AVAILABLE_NORMALISATIONS = frozenset(['none', 'per-read', 'per-chunk'])


def trim_ends_and_filter(ev, trim, min_length, chunk_len):
    if len(ev) < sum(trim) + chunk_len or len(ev) < min_length:
        return None
    else:
        return sloika.util.trim_array(ev, *trim)


def chunkify(ev, chunk_len, kmer_len, use_scaled, normalisation):
    assert len(ev) >= chunk_len

    ml = len(ev) // chunk_len
    ub = ml * chunk_len
    tag = 'scaled_' if use_scaled else ''

    if normalisation == 'per-chunk':
        new_inMat = []
        for chunk_index in range(ml):
            chunk_start = chunk_index * chunk_len
            chunk_finish = chunk_start + chunk_len

            # padding of 1 is needed for features to calculate step deltas
            chunk_finish_maybe_with_padding = min(chunk_finish + 1, len(ev))

            chunk_features = sloika.features.from_events(
                ev[chunk_start : chunk_finish_maybe_with_padding], tag=tag, normalise=True)
            new_inMat.append(chunk_features[:chunk_len])
        new_inMat = np.concatenate(new_inMat)
    else:
        assert normalisation in ['none', 'per-read']
        normalise = normalisation == 'per-read'

        #
        # we may pass bigger ev range to from_events() function than we would
        # actually use later, so that features could be studentized using
        # moments computed using this bigger range; we reset the range in (*) and (**)
        #
        new_inMat = sloika.features.from_events(ev, tag=tag, normalise=normalise)
        new_inMat = new_inMat[0 : ub]  # reset range (*)

    new_inMat = new_inMat.reshape((ml, chunk_len, -1))
    ev = ev[0 : ub]  # reset range (**)

    #
    # 'model' in the name 'model_kmer_len' refers to the model that was used
    # to map the reads read from fast5 file
    #
    model_kmer_len = len(ev['kmer'][0])
    # Use rightmost middle kmer
    kl = (model_kmer_len - kmer_len + 1) // 2
    ku = kl + kmer_len
    kmer_to_state = bio.kmer_mapping(kmer_len, alphabet=b'ACGT')
    new_labels = 1 + np.array([kmer_to_state[k[kl : ku]] for k in ev['kmer']], dtype=np.int32)

    new_labels = new_labels.reshape(ml, chunk_len)
    change = ev['seq_pos'].reshape(ml, chunk_len)
    change = np.apply_along_axis(np.ediff1d, 1, change, to_begin=1)
    new_labels[change == 0] = 0

    new_bad = np.logical_not(ev['good_emission'])
    new_bad = new_bad.reshape(ml, chunk_len)

    assert sloika.util.is_contiguous(new_inMat)
    assert sloika.util.is_contiguous(new_labels)
    assert sloika.util.is_contiguous(new_bad)

    return new_inMat, new_labels, new_bad


def chunk_worker(fn, section, chunk_len, kmer_len, min_length, trim, use_scaled,
                 normalisation):
    """ Chunkifies data for training

    :param fn: A filename to read from
    :param section: Section of read to process (template / complement)
    :param chunk_len: Length of each chunk
    :param kmer_len: Kmer length for training
    :param min_length: Minimum number of events before read can be considered
    :param trim: Tuple (beginning, end) of number of events to trim from read
    :param use_scaled: Use prescaled event statistics
    :param normalisation: Type of normalisation to perform

    :yields: A tuple containing a 3D :class:`ndarray` of size
    (X, chunk_len, nfeatures) containing the features for the batch,
    a 2D :class:`ndarray` of size (X, chunk_len) containing the
    associated labels, and a 2D :class:`ndarray` of size (X, chunk_len)
    indicating bad events.  1 <= X <= batch_size.
    """
    # Import within worker to avoid initialising GPU in main thread
    import sloika.features

    try:
        with fast5.Reader(fn) as f5:
            ev, _ = f5.get_any_mapping_data(section)
    except Exception as e:
        sys.stderr.write('Failed to get mapping data from {}.\n{}\n'.format(fn, repr(e)))
        return None

    ev = trim_ends_and_filter(ev, trim, min_length, chunk_len)
    if ev is None:
        sys.stderr.write('{} is too short.\n'.format(fn))
        return None

    return chunkify(ev, chunk_len, kmer_len, use_scaled, normalisation)


def init_chunk_remap_worker(model, fasta, kmer_len):
    import pickle
    # Import within worker to avoid initialising GPU in main thread
    import sloika.features
    import sloika.transducer
    global calc_post, kmer_to_state, references
    with open(model, 'rb') as fh:
        calc_post = pickle.load(fh)

    references = dict()
    with open(fasta, 'r') as fh:
        for ref in SeqIO.parse(fh, 'fasta'):
            refseq = str(ref.seq)
            if 'N' not in refseq:
                if sys.version_info.major == 3:
                    references[ref.id] = refseq.encode('utf-8')
                else:
                    references[ref.id] = refseq

    kmer_to_state = bio.kmer_mapping(kmer_len, alphabet=b'ACGT')


def remap(read_ref, ev, min_prob, transducer, kmer_len, prior, slip):
    inMat = sloika.features.from_events(ev, tag='')
    inMat = np.expand_dims(inMat, axis=1)
    post = sloika.decode.prepare_post(calc_post(inMat), min_prob=min_prob, drop_bad=(not transducer))

    kmers = np.array(bio.seq_to_kmers(read_ref, kmer_len))
    seq = [kmer_to_state[k] + 1 for k in kmers]
    prior0 = None if prior[0] is None else sloika.util.geometric_prior(len(seq), prior[0])
    prior1 = None if prior[1] is None else sloika.util.geometric_prior(len(seq), prior[1], rev=True)

    score, path = sloika.transducer.map_to_sequence(post, seq, slip=slip,
                                                    prior_initial=prior0,
                                                    prior_final=prior1, log=False)

    ev = nprf.append_fields(ev, ['seq_pos', 'kmer', 'good_emission'],
                            [path, kmers[path], np.repeat(True, len(ev))])

    return (score, ev, path, seq)


def chunk_remap_worker(fn, trim, min_prob, transducer, kmer_len, prior, slip, chunk_len, use_scaled,
                       normalisation, min_length, section, segmentation):
    try:
        with fast5.Reader(fn) as f5:
            ev = f5.get_section_events(section, analysis=segmentation)
            sn = f5.filename_short
    except Exception as e:
        sys.stderr.write('Failure reading events from {}.\n{}\n'.format(fn, repr(e)))
        return None

    try:
        read_ref = references[sn]
    except Exception as e:
        sys.stderr.write('No reference found for {}.\n{}\n'.format(fn, repr(e)))
        return None

    ev = trim_ends_and_filter(ev, trim, min_length, chunk_len)
    if ev is None:
        sys.stderr.write('{} is too short.\n'.format(fn))
        return None

    (score, ev, path, seq) = remap(read_ref, ev, min_prob, transducer, kmer_len, prior, slip)
    (chunks, labels, bad_ev) = chunkify(ev, chunk_len, kmer_len, use_scaled, normalisation)

    return sn + '.fast5', score, len(ev), path, seq, chunks, labels, bad_ev


# TODO: this is a hack, find a nicer way
def trim_open_pore(signal, max_op_fraction=0.3, var_method='mad', window_size=100):
    """Locate raw read in signal by thresholding local variance

    :param signal: raw data containing a read
    :param max_op_fraction: (float) Maximum expected fraction of signal that
        consists of open pore. Higher values will find smaller reads at the
        cost of slightly truncating longer reads.
    :param var_method: ('std' | 'mad') method used to compute the local
        variation. std: standard deviation, mad: Median Absolute Deviation
    :param window_size: size of patches used to estimate local variance
    """
    assert var_method in TRIM_OPEN_PORE_LOCAL_VAR_METHODS, "var_method not understood: {}".format(var_method)

    ml = len(signal) // window_size
    ub = ml * window_size

    if var_method == 'std':
        local_var = signal[:ub].reshape((ml, window_size)).std(1)
    if var_method == 'mad':
        sig_chunks = signal[:ub].reshape((ml, window_size))
        local_var = maths.mad(sig_chunks, axis=1)

    probably_read = (local_var > np.percentile(local_var, 100 * max_op_fraction))
    ix = np.arange(local_var.shape[0])[probably_read]
    start = ix.min() * window_size
    end = (ix.max() + 1) * window_size
    return signal[start:end]
