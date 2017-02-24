#!/usr/bin/env python
from __future__ import print_function
from __future__ import division
from __future__ import absolute_import
from future import standard_library
standard_library.install_aliases()
from builtins import *
from past.utils import old_div
import argparse
import csv
from collections import OrderedDict
import numpy as np
import matplotlib
import os
import pysam
from scipy.stats import gaussian_kde
from scipy.optimize import minimize_scalar
import subprocess
import sys
import traceback
from untangled.cmdargs import proportion, FileExists


parser = argparse.ArgumentParser(
    description='Align reads to reference and output accuracy statistics',
    formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument('--coverage', metavar='proportion', default=0.6, type=proportion,
                    help='Minimum coverage')
# TODO: add several named commonly used values for bwa_mem_args
parser.add_argument('--bwa_mem_args', metavar='args', default='-t 16 -A 1 -B 2 -O 2 -E 1',
                    help="Command line arguments to pass to bwa mem")
parser.add_argument('--mpl_backend', default="Agg", help="Matplotlib backend to use")
parser.add_argument('--figure_format', default="png",
                    help="Figure file format. Must be compatible with matplotlib backend.")
parser.add_argument('reference', action=FileExists,
                    help="Reference sequence to align against")
parser.add_argument('files', metavar='input', nargs='+',
                    help="One or more files containing query sequences")


STRAND = {0 : '+',
          16 : '-'}

QUANTILES = [5, 25, 50, 75, 95]


def call_bwa_mem(fin, fout, genome, clargs=''):
    """Call bwa aligner using the subprocess module

    :param fin: input sequence filename
    :param fout: filename for the output sam file
    :param genome: path to reference to align against
    :param clargs: optional command line arguments to pass to bwa as a string

    :returns: stdout of bwa command

    :raises: subprocess.CalledProcessError
    """
    command_line = "bwa mem {} {} {} > {}".format(clargs, genome, fin, fout)
    try:
        output = subprocess.check_output(command_line,
                                         stderr=subprocess.STDOUT,
                                         shell=True)
    except subprocess.CalledProcessError as e:
        sys.stderr.write("Error calling bwa, exit code {}\n".format(e.returncode))
        sys.stderr.write(e.output + '\n')
        raise
    return output


def samacc(sam, min_coverage=0.6):
    """Read alignments from sam file and return accuracy metrics

    :param sam: filename of input sam file
    :min_coverage: alignments are filtered by coverage

    :returns: list of row dictionaries with keys:
        name1: reference name
        name2: query name
        strand: + or -
        match: number of matches
        mismatch: number of mismatches
        insertion: number of insertions
        deletion: number of deletions
        coverage: query alignment length / query length
        id: identity = sequence matches / alignment matches
        accuracy: sequence matches / alignment length
    """
    res = []
    with pysam.Samfile(sam, 'r') as sf:
        ref_name = sf.references
        for read in sf:
            if read.flag != 0 and read.flag != 16:
                continue

            coverage = old_div(float(read.query_alignment_length), read.query_length)
            if coverage < min_coverage:
                continue

            bins = np.zeros(9, dtype='i4')
            for flag, count in read.cigar:
                bins[flag] += count

            tags = dict(read.tags)
            alnlen = np.sum(bins[:3])
            mismatch = tags['NM']
            correct = alnlen - mismatch

            row = OrderedDict([
                ('name1', ref_name[read.reference_id]),
                ('name2', read.qname),
                ('strand', STRAND[read.flag]),
                ('match', bins[0]),
                ('mismatch', mismatch),
                ('insertion', bins[1]),
                ('deletion', bins[2]),
                ('coverage', coverage),
                ('id', old_div(float(correct), float(bins[0]))),
                ('accuracy', old_div(float(correct), alnlen)),
            ])
            res.append(row)
    return res


def acc_plot(acc, mode, title="Test"):
    """Plot accuracy histogram

    :param acc_dat: list of row dictionaries of basecall accuracy data
    :param title: plot title

    :returns: (figure handle, axes handle)
    """
    f = plt.figure()
    ax = f.add_subplot(111)
    ax.hist(acc, bins=np.arange(0.65, 1.0, 0.01))
    ax.set_xlim(0.65, 1)
    _, ymax = ax.get_ylim()
    ax.plot([mode, mode], [0, ymax], 'r--')
    ax.set_xlabel("Accuracy")
    ax.set_ylabel("Frequency")
    ax.set_title(title)
    return f, ax


def summary(acc_dat, name):
    """Crate summary report and plots for accuracy statistics

    :param acc_dat: list of row dictionaries of read accuracy metrics
    :param name: name for the data set

    :returns: (report string, figure handle, axes handle)
    """
    if len(acc_dat) == 0:
        res = """Summary report for {}:
    No sequences mapped
""".format(name)
        return res, None, None

    acc = np.array([r['accuracy'] for r in acc_dat])
    mean = acc.mean()

    da = gaussian_kde(acc)
    mode = minimize_scalar(lambda x: -da(x), bounds=(0, 1)).x[0]

    qstring1 = ''.join(['{:<11}'.format('Q' + str(q)) for q in QUANTILES]).strip()
    qstring2 = '    '.join(['{:.5f}'.format(v) for v in np.percentile(acc, QUANTILES)])

    a90 = (acc > 0.9).mean()
    n_gt_90 = (acc > 0.9).sum()
    nmapped = len(set([r['name2'] for r in acc_dat]))

    res = """Summary report for {}:
    Number of mapped reads:  {}
    Mean accuracy:  {:.5f}
    Mode accuracy:  {:.5f}
    Accuracy quantiles:
      {}
      {}
    Proportion with accuracy >90%:  {:.5f}
    Number with accuracy >90%:  {}
""".format(name, nmapped, mean, mode, qstring1, qstring2, a90, n_gt_90)

    title = "{} (n = {})".format(name, nmapped)
    f, ax = acc_plot(acc, mode, title)
    return res, f, ax


if __name__ == '__main__':
    args = parser.parse_args()

    # Set the mpl backend. The default, Agg, does not require an X server to be running
    # Note: this must happen before matplotlib.pyplot is imported
    matplotlib.use(args.mpl_backend)
    import matplotlib.pyplot as plt

    for fn in args.files:
        try:
            prefix, suffix = os.path.splitext(fn)
            samfile = prefix + '.sam'
            samaccfile = prefix + '.samacc'
            summaryfile = prefix + '.summary'
            graphfile = prefix + '.' + args.figure_format

            # align sequences to reference
            sys.stdout.write("Aligning {}...\n".format(fn))
            bwa_output = call_bwa_mem(fn, samfile, args.reference, args.bwa_mem_args)
            sys.stdout.write(bwa_output)

            # compile accuracy metrics
            acc_dat = samacc(samfile, min_coverage=args.coverage)
            if len(acc_dat) > 0:
                with open(samaccfile, 'w') as fs:
                    fields = list(acc_dat[0].keys())
                    writer = csv.DictWriter(fs, fieldnames=fields, delimiter=' ')
                    writer.writeheader()
                    for row in acc_dat:
                        writer.writerow(row)

            # write summary file and plot
            report, f, ax = summary(acc_dat, name=fn)
            if f is not None:
                f.savefig(graphfile)
            sys.stdout.write('\n' + report + '\n')
            with open(summaryfile, 'w') as fs:
                fs.writelines(report)
        except:
            sys.stderr.write("{}: something went wrong, skipping\n\n".format(fn))
            sys.stderr.write("Traceback:\n\n{}\n\n".format(traceback.format_exc()))
            continue
