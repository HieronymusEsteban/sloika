#!/usr/bin/env python
import argparse
from six.moves import cPickle
import sys
import time

import theano as th
import theano.tensor as T

from dragonet.bio import seq_tools

import numpy as np
from tang.fast5 import iterate_fast5, fast5
from sloika import layers, networks, updates
from tang.util.cmdargs import (AutoBool, display_version_and_exit, FileExist,
                               NonNegative, ParseToNamedTuple, Positive,
                               probability, TypeOrNone)

# This is here, not in main to allow documentation to be built
parser = argparse.ArgumentParser(
    description='Mock basecaller for Tang NN library',
    formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument('--bad', default=False, action=AutoBool, help='Label bad emissions')
parser.add_argument('--batch', default=1000, metavar='size', type=Positive(int),
    help='Batch size (number of chunks to run in parallel)')
parser.add_argument('--chunk', default=100, metavar='events', type=Positive(int),
    help='Length of each read chunk')
parser.add_argument('--drop_runs', metavar='length', type=Positive(int), default=10,
    help='Drop chunks with runs longer than length')
parser.add_argument('--edam', nargs=3, metavar=('rate', 'decay1', 'decay2'),
    default=(0.1, 0.9, 0.99), type=(NonNegative(float), NonNegative(float), NonNegative(float)),
    action=ParseToNamedTuple, help='Parameters for Exponential Decay Adaptive Momementum')
parser.add_argument('--kmer', default=3, metavar='length', type=Positive(int),
    help='Length of kmer to estimate')
parser.add_argument('--limit', default=None, type=TypeOrNone(Positive(int)),
    help='Limit number of reads to process.')
parser.add_argument('--lrdecay', default=None, metavar='epochs', type=Positive(float),
    help='Number of epochs over which learning rate is halved')
parser.add_argument('--model', metavar='file', action=FileExist,
    help='File to read model from')
parser.add_argument('--niteration', metavar='epochs', type=Positive(int), default=500,
    help='Maximum number of epochs to train for')
parser.add_argument('--save_every', metavar='x', type=Positive(int), default=5,
    help='Save model every x epochs')
parser.add_argument('--sd', default=0.1, metavar='value', type=Positive(float),
    help='Standard deviation to initialise with')
parser.add_argument('--section', default='template', choices=['template', 'complement'],
    help='Section to call')
parser.add_argument('--strand_list', default=None, action=FileExist,
    help='strand summary file containing subset.')
parser.add_argument('--trim', default=(500, 50), nargs=2, type=Positive(int),
    metavar=('beginning', 'end'), help='Number of events to trim off start and end')
parser.add_argument('--validation', default=None, type=probability,
    help='Proportion of reads to use for validation')
parser.add_argument('--version', nargs=0, action=display_version_and_exit,
    help='Display version information.')
parser.add_argument('--window', default=3, type=Positive(int), metavar='length',
    help='Window length for input features')
parser.add_argument('output', help='Prefix for output files')
parser.add_argument('input_folder', action=FileExist,
    help='Directory containing single-read fast5 files.')

_ETA = 1e-300
_NBASE = 4


def wrap_network(network):
    x = T.tensor3()
    labels = T.imatrix()
    rate = T.scalar()
    post = network.run(x)
    loss = T.mean(th.map(T.nnet.categorical_crossentropy, sequences=[post, labels])[0])
    ncorrect = T.sum(T.eq(T.argmax(post,  axis=2), labels))
    update_dict = updates.edam(network, loss, rate, (args.edam.decay1, args.edam.decay2))
    # update_dict = updates.sgd(network, loss, rate, args.edam.decay1)

    fg = th.function([x, labels, rate], [loss, ncorrect], updates=update_dict)
    fv = th.function([x, labels], [loss, ncorrect])
    return fg, fv

def max_rle(x):
    pos, = np.where(np.ediff1d(x, to_begin=1) != 0)
    return np.amax(np.diff(np.append(pos, len(x)))[x[pos]])

def chunk_events(files, max_len, permute=True):
    _, kmer_to_state = seq_tools.all_kmers(length=args.kmer, rev_map=True)
    black_list = set()

    pfiles = list(files)
    if permute:
        pfiles = np.random.permutation(pfiles)

    in_mat = labels = None
    for fn in pfiles:
        try:
            with fast5(fn) as f5:
                ev, _ = f5.get_any_mapping_data(args.section)
        except:
            black_list.add(fn)
            sys.stderr.write('Failed to read from {}.\n'.format(fn))
            continue
        if len(ev) <= sum(args.trim) + args.chunk:
            continue

        new_inMat = features.from_events(ev)[args.trim[0] : -args.trim[1]]
        ml = len(new_inMat) // args.chunk
        new_inMat = new_inMat[:ml * args.chunk].reshape((ml, args.chunk, -1))


        model_kmer = len(ev['kmer'][0])
        l = args.trim[0]
        u = l + args.chunk * ml
        kl = (model_kmer - args.kmer) // 2
        ku = kl + args.kmer
        new_labels = np.array(map(lambda k: kmer_to_state[k[kl:ku]], ev['kmer'][l:u]), dtype=np.int32)
        if args.bad:
            new_labels[np.logical_not(ev['good_emission'][l:u])] = _NBASE * args.kmer
        new_labels = new_labels.reshape((ml, args.chunk))
        new_labels = new_labels[:, (args.window // 2) : -(args.window // 2)]

        accept = np.apply_along_axis(max_rle, 1, new_labels == _NBASE) < args.drop_runs
        new_inMat = new_inMat[accept]
        new_labels = new_labels[accept]

        in_mat = np.vstack((in_mat, new_inMat)) if in_mat is not None else new_inMat
        labels = np.vstack((labels, new_labels)) if labels is not None else new_labels
        if len(in_mat) > max_len:
            yield np.ascontiguousarray(in_mat.transpose((1,0,2))), np.ascontiguousarray(labels.transpose())
            in_mat = None
            labels = None

    if in_mat is not None:
        yield np.ascontiguousarray(in_mat.transpose((1,0,2))), np.ascontiguousarray(labels.transpose())

    files -= black_list


if __name__ == '__main__':
    args = parser.parse_args()
    kmers = seq_tools.all_kmers(length=args.kmer)

    if args.model is not None:
        with open(args.model, 'r') as fh:
            network = cPickle.load(fh)
    else:
        network = networks.nanonet(kmer=args.kmer, winlen=args.window, sd=args.sd, bad_state=args.bad)
    fg, fv = wrap_network(network)

    train_files = set(iterate_fast5(args.input_folder, paths=True, limit=args.limit, strand_list=args.strand_list))
    if args.validation is not None:
        nval = int(args.validation * len(train_files))
        val_files = set(np.random.choice(list(train_files), size=nval, replace=False))
        train_files -= val_files

    score = wscore = 0.0
    acc = wacc = 0.0
    SMOOTH = 0.8
    learning_rate = args.edam.rate
    learning_factor = 0.5 ** (1.0 / args.lrdecay) if args.lrdecay is not None else 1.0
    for it in xrange(1, args.niteration):
        print '* Epoch {}: learning rate {:6.2e}'.format(it, learning_rate)
        #  Training
        total_ev = 0
        t0 = time.time()
        for i, in_data in enumerate(chunk_events(train_files, args.batch)):
            fval, ncorr = fg(in_data[0], in_data[1], learning_rate)
            fval = float(fval)
            ncorr = float(ncorr)
            nev = in_data[1].shape[0] * in_data[1].shape[1]
            total_ev += nev
            score = fval + SMOOTH * score
            acc = (ncorr / nev) + SMOOTH * acc
            wscore = 1.0 + SMOOTH * wscore
            wacc = 1.0 + SMOOTH * wacc
        tn = time.time()
        print '  training   {:5.3f}   {:5.2f}% ... {:6.1f}s ({:.2f} kev/s)'.format(score / wscore, 100.0 * acc / wacc, tn - t0, 0.001 * total_ev / (tn - t0))

        #  Validation
        t0 = time.time()
        vscore = vnev = vncorr = 0
        for i, in_data in enumerate(chunk_events(val_files, args.batch)):
            fval, ncorr = fv(in_data[0], in_data[1])
            fval = float(fval)
            ncorr = float(ncorr)
            nev = in_data[1].shape[0] * in_data[1].shape[1]
            vscore += fval * nev
            vncorr += ncorr
            vnev += nev
        tn = time.time()
        print '  validation {:5.3f}   {:5.2f}% ... {:6.1f}s ({:.2f} kev/s)'.format(vscore / vnev, 100.0 * vncorr / vnev, tn - t0, 0.001 * vnev / (tn - t0))

        # Save model
        if (it % args.save_every) == 0:
            with open(args.output + '_epoch{:05d}.pkl'.format(it), 'wb') as fh:
                cPickle.dump(network, fh, protocol=cPickle.HIGHEST_PROTOCOL)

        learning_rate *= learning_factor

    with open(args.output + '_final.pkl', 'wb') as fh:
        cPickle.dump(network, fh, protocol=cPickle.HIGHEST_PROTOCOL)
