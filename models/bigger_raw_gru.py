import sloika.module_tools as smt


def network(klen, sd, nfeature=1, winlen=11, stride=2, size=[32, 96, 128]):
    """ Create fat Nanonet with GRUs and convolution input layer

    :param klen: Length of kmer
    :param sd: Standard Deviation of initialisation noise
    :param nfeature: Number of features per time-step
    :param winlen: Length of convolution window over data
    :param stride: Stride over data
    :param size: Sizes of hidden recurrent layers

    :returns: a `class`:layer.Layer:
    """
    _prn = smt.partial(smt.truncated_normal, sd=sd)
    nstate = smt.nstate(klen)
    gru_act = smt.tanh
    ff_act = smt.tanh

    inlayer = smt.Convolution(nfeature, size[0], winlen, stride, init=_prn, has_bias=True, fun=ff_act)

    fwd1 = smt.Gru(size[0], size[1], init=_prn, has_bias=True, fun=gru_act)
    bwd1 = smt.Gru(size[0], size[1], init=_prn, has_bias=True, fun=gru_act)
    layer1 = smt.birnn(fwd1, bwd1)

    layer2 = smt.FeedForward(2 * size[1], size[2], has_bias=True, fun=ff_act)

    fwd3 = smt.Gru(size[2], size[1], init=_prn, has_bias=True, fun=gru_act)
    bwd3 = smt.Gru(size[2], size[1], init=_prn, has_bias=True, fun=gru_act)
    layer3 = smt.birnn(fwd3, bwd3)

    layer4 = smt.FeedForward(2 * size[1], size[2], init=_prn, has_bias=True, fun=ff_act)

    outlayer = smt.Softmax(size[2], nstate, init=_prn, has_bias=True)

    return smt.Serial([inlayer, layer1, layer2, layer3, layer4, outlayer])
