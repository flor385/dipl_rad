"""
Module for evaluating the RBM energy-based neural net
language models on the Microsoft
Sentences Completion Challenge dataset (obtained through
the 'data' module).
"""

import os
import sys
import util
import logging

import matplotlib.pyplot as plt
import theano
import numpy as np

import data
from lrbm import LRBM
from ngram import NgramModel


#   dir where we store NNet models
_DIR = 'nnet_models'

#   how many ngrams from the validation set should be used
#   when evaluating exact log-likelihood... the whole validation
#   set can't be used because this is SLOW
_LOG_LIK_SIZE = 100


#   logger
log = logging.getLogger(__name__)


def random_ngrams(ngrams, vocab_size, all=False, dist=None, shuffle=False):
    """
    Given an array of ngrams, creates a copy of that array
    with some terms (columns) randomized.

    :param ngrams: A numpy array of ngrams, of shape (N, n),
        where N is number of ngrams, and n is ngram length.
    :param vocab_size: Vocabulary size.
    :param all: If all ngram terms should be randomized, or just
        the conditioned one.
    :param shuffle: If randomization should be done by shuffling,
        or by sampling.
    :param dist: Probability distribution of words in the vocabulary.
        If None, uniform sampling is used.

    """
    r_val = np.array(ngrams)

    #   iterate through the terms that need replacing
    for term in xrange(ngrams.shape[1] if all else 1):

        #   iterate through the features of the term
        if shuffle:
            np.random.shuffle(r_val[:, term])
        else:
            r_val[:, term] = np.random.choice(
                vocab_size, ngrams.shape[0], p=dist).astype('uint16')

    return r_val


def dataset_split(x, validation=0.05, test=0.05, rng=None):
    """
    Splits dataset into train, validation and testing subsets.
    The dataset is split on the zeroth axis.

    :param x: The dataset of shape (N, ...)
    :param validation: float in range (0, 1) that indicates
        desired validation set size to be N * validation
    :param test: float in range (0, 1) that indicates
        desired test set size to be N * test
    :param rng: Numpy random number generator, or an integer
        seed for rng, or None (rng initialized always with the same seed).
    """
    assert validation > 0. and test > 0.
    assert validation + test < 1.

    log.info("Performing dataset split, validation size: %.2f, "
             "test size: %.2f", validation, test)

    if rng is None:
        rng = np.random.RandomState()
    elif isinstance(rng, int):
        rng = np.random.RandomState(rng)

    #   shuffle data
    rng.shuffle(x)

    #   generate split indices
    i1 = int(x.shape[0] * (1. - validation - test))
    i2 = int(x.shape[0] * (1. - test))

    return x[:i1], x[i1:i2], x[i2:]


def main():
    """
    Trains and evaluates RBM energy based neural net
    language models on the Microsoft Sentence Completion
    Challenge dataset.

    Allowed cmd-line flags:
        -s TS_FILES : Uses the reduced trainsed (TS_FILES trainset files)
        -o MIN_OCCUR : Only uses terms that occur MIN_OCCUR or more times
            in the trainset. Other terms are replaced with a special token.
        -f MIN_FILES : Only uses terms that occur in MIN_FILES or more files
            in the trainset. Other terms are replaced with a special token.
        -n : n-gram length (default 4)
        -t : Use tree-grams (default does not ues tree-grams)
        -u FTRS : Features to use. FTRS must be a string composed of zeros
            and ones, of length 5. Ones indicate usage of following features:
            (word, lemma, google_pos, penn_pos, dependency_type), respectively.

    Neural-net specific cmd-line flags:
        -ep EPOCHS : Number of training epochs, defaults to 20.
        -a ALPHA : The alpha parameter of the LRBM, defaults to 0.5
        -eps EPS : Learning rate, defaults to 0.005.
        -mnb MBN_SIZE : Size of the minibatch, defaults to 2000.

    """
    logging.basicConfig(level=logging.DEBUG)
    log.info("RBM energy-based neural net language model")

    #   get the data handling parameters
    ts_reduction = util.argv('-s', None, int)
    min_occ = util.argv('-o', 5, int)
    min_files = util.argv('-f', 2, int)
    n = util.argv('-n', 4, int)
    use_tree = '-t' in sys.argv
    ft_format = lambda s: map(
        lambda s: s.lower() in ["1", "true", "yes", "t", "y"], s)
    ftr_use = np.array(util.argv('-u', ft_format("001000"), ft_format))

    #   nnet rbm-s only support one-feature ngrams
    assert ftr_use.sum() == 1

    #   the directory that stores ngram models we compare against
    ngram_dir = NgramModel.dir(use_tree, ftr_use, ts_reduction,
                               min_occ, min_files)

    #   get nnet training parameters
    epochs = util.argv('-ep', 20, int)
    alpha = util.argv('-a', 0.5, float)
    eps = util.argv('-eps', 0.002, float)
    mnb_size = util.argv('-mnb', 2000, int)
    n_hid = util.argv('-h', 1000, int)
    d = util.argv('-d', 100, int)

    #   load data
    ngrams, q_groups, answers, feature_sizes = data.load_ngrams(
        n, ftr_use, use_tree, subset=ts_reduction,
        min_occ=min_occ, min_files=min_files)
    used_ftr_sizes = feature_sizes[ftr_use]
    #   remember, we only use one feature
    vocab_size = used_ftr_sizes[0]
    log.info("Data loaded, %d ngrams", ngrams.shape[0])

    #   split data into sets
    x_train, x_valid, x_test = dataset_split(ngrams, 0.05, 0.05, rng=12345)

    #   generate a version of the validation set that has
    #   the first term (the conditioned one) randomized
    #   w.r.t. unigram distribution
    #   so first create the unigram distribution, no smoothing
    unigrams_data = data.load_ngrams(1, ftr_use, False, subset=ts_reduction,
                                     min_occ=min_occ, min_files=min_files)[0]
    unigrams_data = NgramModel.get(1, False, ftr_use, feature_sizes,
                                   unigrams_data, ngram_dir, lmbd=0.0)
    unigrams_dist = unigrams_data.probability(
        np.arange(vocab_size).reshape(vocab_size, 1))
    unigrams_dist /= unigrams_dist.sum()
    #   finally, generate validation sets with randomized term
    x_valid_r = random_ngrams(x_valid, vocab_size, False, unigrams_dist)

    #   the directory for this model
    dir = "%s_%d-gram_features-%s_data-subset_%r-min_occ_%r-min_files_%r" % (
        "tree" if use_tree else "linear", n,
        "".join([str(int(b)) for b in ftr_use]),
        ts_reduction, min_occ, min_files)
    dir = os.path.join(_DIR, dir)
    if not os.path.exists(dir):
        os.makedirs(dir)

    #   filename base for this model
    file = "nhid-%d_d-%d_train_mnb-%d_epochs-%d_eps-%.5f_alpha-%.2f" % (
        n_hid, d, mnb_size, epochs, eps, alpha)

    #   store the logs
    log_file_handler = logging.FileHandler(os.path.join(dir, file + ".log"))
    log_file_handler.setLevel(logging.INFO)
    logging.root.addHandler(log_file_handler)

    #   we will plot log-lik ratios for every 10 minibatches
    #   we will also plot true mean log-lik
    x_valid_ll_ratio = []
    x_valid_ll = []

    def mnb_callback(lrbm, epoch, mnb):
        """
        Callback function called after every minibatch.
        """
        if (mnb % 10) != 9:
            return

        #   calculate log likelihood using the exact probability
        probability_f = theano.function([lrbm.input], lrbm.probability)
        x_valid_ll.append(np.log(probability_f(x_valid[_LOG_LIK_SIZE])).mean())

        #   also calculate the probability ratio between normal validation set
        #   and the randomized one
        unnp_f = theano.function([lrbm.input], lrbm.unnp)
        x_valid_ll_ratio.append(
            np.log(unnp_f(x_valid) / unnp_f(x_valid_r)).mean())

        log.info('Epoch %d, mnb: %d, x_valid mean-log-lik: %.5f'
                 ' , log(p(x_valid) / p(x_valid_r).mean(): %.5f',
                 epoch, mnb, x_valid_ll[-1], x_valid_ll_ratio[-1])

    #   track if the model progresses on the sentence completion challenge
    sent_challenge = []

    def epoch_callback(lrbm, epoch):

        #   log some info about the parameters, just so we know
        param_mean_std = [(k, v.mean(), v.std())
                          for k, v in lrbm.params().iteritems()]
        log.info("Epoch %d: %s", epoch, "".join(
            ["\n\t%s: %.5f +- %.5f" % pms for pms in param_mean_std]))

        #   evaluate model on the sentence completion challenge
        unnp_f = theano.function([lrbm.input], lrbm.unnp)
        qg_log_lik = [[np.log(unnp_f(q)).sum() for q in q_g]
                      for q_g in q_groups]
        predictions = map(lambda q_g: np.argmax(q_g), qg_log_lik)
        sent_challenge.append((np.array(predictions) == answers).mean())
        log.info('Epoch %d sentence completion eval score: %.4f',
                 epoch, sent_challenge[-1])

    log.info("Creating LRBM")
    lrbm = LRBM(n, vocab_size, d, n_hid, 12345)
    lrbm.mnb_callback = mnb_callback
    lrbm.epoch_callback = epoch_callback
    train_cost, valid_cost, _ = lrbm.train(
        x_train, x_valid, mnb_size, epochs, eps, alpha)

    #   plot many pretty things
    mnb_count = (x_train.shape[0] - 1) / mnb_size + 1
    fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(
        2, 2, sharex=True, sharey=False)
    fig.set_size_inches(16, 12)
    ax1.plot(mnb_count * (np.arange(epochs) + 1), train_cost, 'b-',
             label='train')
    ax1.plot(mnb_count * (np.arange(epochs) + 1), valid_cost, 'g-',
             label='valid')
    ax1.set_title('Cost')
    plt.legend(loc=2)
    ax2.plot(10 * np.arange(len(x_valid_ll)), x_valid_ll, 'g-')
    ax2.set_title('log-lik(x_valid)')
    ax3.plot(10 * np.arange(len(x_valid_ll)), x_valid_ll_ratio, 'g-')
    ax3.set_title('log(p(x_valid) / p(x_valid_r)).mean()')
    ax4.plot(mnb_count * np.arange(epochs + 1), sent_challenge, 'g-')
    ax4.set_title('sent_challenge')
    plt.savefig(file + ".pdf")


if __name__ == '__main__':
    main()
