#!/usr/bin/env python
import argparse
import cPickle
import h5py
import imp
import numpy as np
import os
from shutil import copyfile
import sys
import time

import theano as th
import theano.tensor as T

from untangled.cmdargs import (AutoBool, display_version_and_exit, FileExists,
                               NonNegative, ParseToNamedTuple, Positive)

from sloika import updates, __version__

# This is here, not in main to allow documentation to be built
parser = argparse.ArgumentParser(
    description='Train a simple transducer neural network',
    formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument('--bad', default=True, action=AutoBool,
    help='Use bad events as a separate state')
parser.add_argument('--batch', default=100, metavar='size', type=Positive(int),
    help='Batch size (number of chunks to run in parallel)')
parser.add_argument('--adam', nargs=3, metavar=('rate', 'decay1', 'decay2'),
    default=(1e-3, 0.9, 0.999), type=(NonNegative(float), NonNegative(float), NonNegative(float)),
    action=ParseToNamedTuple, help='Parameters for Exponential Decay Adaptive Momementum')
parser.add_argument('--lrdecay', default=5000, metavar='batches', type=Positive(float),
    help='Number of batches over which learning rate is halved')
parser.add_argument('--niteration', metavar='batches', type=Positive(int), default=500000,
    help='Maximum number of batches to train for')
parser.add_argument('--save_every', metavar='x', type=Positive(int), default=5000,
    help='Save model every x batches')
parser.add_argument('--sd', default=0.1, metavar='value', type=Positive(float),
    help='Standard deviation to initialise with')
parser.add_argument('--version', nargs=0, action=display_version_and_exit, metavar=__version__,
    help='Display version information.')
parser.add_argument('model', metavar='file.py', action=FileExists,
    help='File to read python model description from')
parser.add_argument('output', help='Prefix for output files')
parser.add_argument('input', action=FileExists,
    help='HDF5 file containing chunks')


def wrap_network(network):
    x = T.tensor3()
    labels = T.imatrix()
    rate = T.scalar()
    post = network.run(x)
    loss = T.mean(th.map(T.nnet.categorical_crossentropy, sequences=[post, labels])[0])
    ncorrect = T.sum(T.eq(T.argmax(post,  axis=2), labels))
    update_dict = updates.adam(network, loss, rate, (args.adam.decay1, args.adam.decay2))

    fg = th.function([x, labels, rate], [loss, ncorrect], updates=update_dict)
    fv = th.function([x, labels], [loss, ncorrect])
    return fg, fv


if __name__ == '__main__':
    args = parser.parse_args()

    os.mkdir(args.output)
    copyfile(args.model, os.path.join(args.output, 'model.py'))

    log = open(os.path.join(args.output, 'model.log'), 'w', 0)

    log.write('* Command line\n')
    log.write(' '.join(sys.argv) + '\n')

    log.write('* Creating network from {}\n'.format(args.model))
    with h5py.File(args.input, 'r') as h5:
        klen =h5.attrs['kmer']
    netmodule = imp.load_source('netmodule', args.model)
    network = netmodule.network(klen=klen, sd=args.sd)
    fg, fv = wrap_network(network)

    log.write('* Loading data from {}\n'.format(args.input))
    with h5py.File(args.input, 'r') as h5:
        full_chunks = h5['chunks'][:]
        full_labels = h5['labels'][:]
        if args.bad:
            full_labels[h5['bad'][:]] = 0

    total_ev = 0
    score = wscore = 0.0
    acc = wacc = 0.0
    SMOOTH = 0.8
    learning_rate = args.adam.rate
    learning_factor = 0.5 ** (1.0 / args.lrdecay) if args.lrdecay is not None else 1.0

    t0 = time.time()
    log.write('* Training\n')
    for i in xrange(args.niteration):
        idx = np.sort(np.random.choice(len(full_chunks), size=args.batch, replace=False))
        events = np.ascontiguousarray(full_chunks[idx].transpose((1, 0, 2)))
        labels = np.ascontiguousarray(full_labels[idx].transpose())

        fval, ncorr = fg(events, labels, learning_rate)
        fval = float(fval)
        ncorr = float(ncorr)
        nev = np.size(labels)
        total_ev += nev
        score = fval + SMOOTH * score
        acc = (ncorr / nev) + SMOOTH * acc
        wscore = 1.0 + SMOOTH * wscore
        wacc = 1.0 + SMOOTH * wacc


        # Save model
        if (i + 1) % args.save_every == 0:
            log.write('C')
            with open(os.path.join(args.output, 'model_checkpoint_{:05d}.pkl'.format((i + 1) // args.save_every)), 'wb') as fh:
                cPickle.dump(network, fh, protocol=cPickle.HIGHEST_PROTOCOL)
        else:
            log.write('.')

        if (i + 1) % 50 == 0:
            tn = time.time()
            dt = tn - t0
            log.write(' {:5d} {:5.3f}  {:5.2f}%  {:5.2f}s ({:.2f} kev/s)\n'.format((i + 1) // 50, score / wscore, 100.0 * acc / wacc, dt, total_ev / 1000.0 / dt))
            total_ev = 0
            t0 = tn

        learning_rate *= learning_factor

    with open(os.path.join(args.output,  'model_final.pkl'), 'wb') as fh:
        cPickle.dump(network, fh, protocol=cPickle.HIGHEST_PROTOCOL)
