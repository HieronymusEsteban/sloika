import numpy as np

_NEG_LARGE = -50000.0

def argmax(*args):
    res = max(enumerate(args), key=lambda x: x[1])
    return res

def align(trans1, trans2, gapin, gap, gapout, rev=True):
    """  Perform an alignment of two partial transducers.
    Simple unoptimised implementation

    :param trans1: Transducer (nevent x nstate) log-valued posteriors
    :param trans2: Transducer (nevent x nstate) log-valued posteriors
    :param gapin: gap penalty for non-aligned event around hairpin
    :param gap: gap penalty where template and complement are aligned
    :param gapout: gap penalty for non-aligned events at end of strand
    :param rev: Reverse and complement first transducer

    :Notes: States of the pair-HMM (second axis of the tensor) are
    `0` XX -- both transducers move or stay
    `1` s- -- first transducer stays while second skips
    `2` X- -- first transducer moves while second skips
    `3` -s -- second transducer stays while first skips
    `4` -X -- second transducer moves while first skips

    For template to complement calls, the template (first) transducer is
    reverse complemented *in place*.  Reversing in this fashion changes the
    meaning of the stay state and so the template transducer must have been
    trained correctly.

    :returns: Tuple of score and path
    """
    nev1 = len(trans1)
    nev2 = len(trans2)
    assert trans1.shape[1] == 5, 'Incorrect number of states in first transducer'
    assert trans2.shape[1] == 5, 'Incorrect number of states in second transducer'
    if rev:
        #  Reverse complement first transducer if required
        trans1 = trans1[::-1, [3, 2, 1, 0, 4]]

    vmat = np.empty((nev1 + 1, nev2 + 1, 5), dtype=np.float32)
    vmat.fill(_NEG_LARGE)
    imat = np.empty((nev1 + 1, nev2 + 1, 5), dtype=np.int8)
    imat.fill(-1)

    all1 = np.amax(trans1, axis=1)
    all2 = np.amax(trans2, axis=1)
    move1 = np.amax(trans1[:, :-1], axis=1)
    move2 = np.amax(trans2[:, :-1], axis=1)

    #  Initial row and column
    vmat[0, 0, 0] = 0
    vmat[1:, 0, 2] = np.cumsum(all1) + gapin + np.arange(nev1) * gapin
    vmat[0, 1:, 4] = np.cumsum(all2) + gapin + np.arange(nev2) * gapin
    imat[1, 0 , 2] = 0
    imat[0, 1, 4] = 0
    imat[2:, 0, 2] = 2
    imat[0, 2:, 4] = 4

    for i1 in xrange(nev1):
        for i2 in xrange(nev2):
            if i1 + 1 == nev1 or i2 + 1 == nev2:
                gs = gapout
            else:
                gs = gap
            trans = trans1[i1] + trans2[i2]
            x = np.amax(trans)
            m = np.amax(trans[:-1])

            # match state (diagonal move)
            i, v = argmax(vmat[i1, i2, 0] + x,
                          vmat[i1, i2, 1] + x,
                          vmat[i1, i2, 2] + m,
                          vmat[i1, i2, 3] + x,
                          vmat[i1, i2, 4] + m)
            vmat[i1 + 1, i2 + 1, 0] = v
            imat[i1 + 1, i2 + 1, 0] = i

            # stay-skip state (vertical move)
            i, v = argmax(vmat[i1, i2 + 1, 0] + gs + trans1[i1][4],
                          vmat[i1, i2 + 1, 1] + gs + trans1[i1][4])
            vmat[i1 + 1, i2 + 1, 1] = v
            imat[i1 + 1, i2 + 1, 1] = i

            # emit-skip state (vertical move)
            i, v = argmax(vmat[i1, i2 + 1, 0] + gs + move1[i1],
                          vmat[i1, i2 + 1, 1] + gs + move1[i1],
                          vmat[i1, i2 + 1, 2] + gs + all1[i1],
                          vmat[i1, i2 + 1, 3] + gs + move1[i1],
                          vmat[i1, i2 + 1, 4] + gs + move1[i1])
            vmat[i1 + 1, i2 + 1, 2] = v
            imat[i1 + 1, i2 + 1, 2] = i

            # skip-stay state (horizontal move)
            i, v = argmax(vmat[i1 + 1, i2, 0] + gs + trans2[i2][4],
                          vmat[i1 + 1, i2, 3] + gs + trans2[i2][4])
            vmat[i1 + 1, i2 + 1, 3] = v
            imat[i1 + 1, i2 + 1, 3] = 0 if i ==0 else i + 2

            # skip-emit state (horizontal move)
            # (small penalty so 4 -> 2 is favoured over 2 -> 4)
            PEN = -1e-4
            i, v = argmax(vmat[i1 + 1, i2, 0] + gs + move2[i2],
                          vmat[i1 + 1, i2, 1] + gs + move2[i2] + PEN,
                          vmat[i1 + 1, i2, 2] + gs + move2[i2] + PEN,
                          vmat[i1 + 1, i2, 3] + gs + move2[i2],
                          vmat[i1 + 1, i2, 4] + gs + all2[i2])
            vmat[i1 + 1, i2 + 1, 4] = v
            imat[i1 + 1, i2 + 1, 4] = i

    # Back trace to find path
    i1 = nev1
    i2 = nev2
    score = np.amax(vmat[i1, i2])
    path = [np.argmax(vmat[i1, i2])]
    while i1 > 0 or i2 > 0:
        assert i1 >=0 and i2 >= 0, 'Failed i1 {} i2 {}\n'.format(i1, i2)
        move = (path[-1] + 1) // 2
        pfrom = imat[i1, i2, path[-1]]
        if move == 0:
            # Diagonal move
            i1 -= 1
            i2 -= 1
        elif move == 1:
            # Vertical move
            i1 -= 1
        elif move == 2:
            # Horizontal move
            i2 -= 1
        path += [pfrom]

    return score, path[:-1][::-1]


def alignment_to_call(trans1, trans2, alignment, rev=True):
    """  Convert a transducer alignment into a series of state calls

    :param trans1: Transducer (nevent x nstate) log-valued posteriors
    :param trans2: Transducer (nevent x nstate) log-valued posteriors
    :param alignment: Alignment of trans1 and trans2
    :param rev: Reverse and complement first transducer

    :returns:
    """
    stay_state = 4
    if rev:
        #  Reverse complement first transducer if required
        trans1 = trans1[::-1, [3, 2, 1, 0, 4]]
    emit1 = np.argmax(trans1, axis=1)
    emit2 = np.argmax(trans2, axis=1)

    states = []
    pos1 = 0
    pos2 = 0
    for move in alignment:
        assert pos1 <= len(trans1), 'Dropped off end of sequence 1'
        assert pos2 <= len(trans2), 'Dropped off end of sequence 2'
        if move == 0:
            # 'Diagonal' -- both sequences emit
            state = np.argmax(trans1[pos1] + trans2[pos2])
            states.append(state)
            pos1 += 1
            pos2 += 1
        elif move == 1:
            # 'Vertical' -- first sequence stays, second skips
            states.append(stay_state)
            pos1 += 1
        elif move == 2:
            # 'Vertical' -- first sequence emits, second skips
            states.append(emit1[pos1])
            pos1 += 1
        elif move == 3:
            # 'Horizontal' -- second sequence stays, first skips
            states.append(stay_state)
            pos2 += 1
        elif move == 4:
            # 'Horizontal' -- second sequence emits, first skips
            states.append(emit2[pos2])
            pos2 += 1
        else:
            assert False, 'Invalid move {} detected\n'.format(move)

    return states



#  First four states encode stays, so all other states are offset by 3 relative
# to transducer output.
_STATE_TO_BASE = np.tile(range(4), 7)
def decode_full_transducer(ltrans):
    """  Decode a 'full transducer'

    :param ltrans: A 3D :class:`nd.array` containing logarithm of transducer
    probabilities.

    :returns: A tuple containing score and path
    """
    nev = len(trans)
    assert trans.shape == (nev, 4, 25), "Transducer has incorrect shape"

    #  Viterbi forwards path
    vitmat = np.empty((nev + 1, 4))
    vitmat.fill(-5e8)  # Canary value
    imat = np.empty((nev + 1, 4), dtype=np.int8)
    imat.fill(-1) # Canary value
    vitmat[0] = 0.0
    for i in xrange(1, nev + 1):
        for pre in xrange(4):
            #  Stay state
            vitmat[i, pre] = vitmat[i - 1, pre] + ltrans[i - 1, 0]
            imat[i, pre] = pre
        for pre in xrange(4):
            for j in xrange(1, 25):
                st = _STATE_TO_BASE[j + 3]
                if vitmat[i - 1, pre] + ltrans[i - 1, j] > vitmat[i, st]:
                    vitmat[i, st] = vitmat[i - 1, pre] + ltrans[i - 1, j]
                    imat[i, st] = j + 3

    #  Backtrace
    path = np.empty(nev + 1, dtype=np.int8)
    score = np.amax(vitmat[-1])
    path[-1] = np.argmax(vitmat[-1])
    for i in xrange(nev, 0, -1):
        path[i - 1] = imat[i, _STATE_TO_BASE[path[i]]]
        assert path[i - 1] >=0

    return score, path

