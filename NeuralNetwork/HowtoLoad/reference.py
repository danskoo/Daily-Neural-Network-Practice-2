# Author: Kyle Kastner
# License: BSD 3-Clause
# For a reference on parallel processing in Python see tutorial by David Beazley
# http://www.slideshare.net/dabeaz/an-introduction-to-python-concurrency
# Loosely based on IBM example
# http://www.ibm.com/developerworks/aix/library/au-threadingpython/
# If you want to download all the PASCAL VOC data, use the following in bash...
"""
#! /bin/bash
# 2008
wget http://host.robots.ox.ac.uk/pascal/VOC/voc2008/VOCtrainval_14-Jul-2008.tar
# 2009
wget http://host.robots.ox.ac.uk/pascal/VOC/voc2009/VOCtrainval_11-May-2009.tar
# 2010
wget http://host.robots.ox.ac.uk/pascal/VOC/voc2010/VOCtrainval_03-May-2010.tar
# 2011
wget http://host.robots.ox.ac.uk/pascal/VOC/voc2011/VOCtrainval_25-May-2011.tar
# 2012
wget http://host.robots.ox.ac.uk/pascal/VOC/voc2012/VOCtrainval_11-May-2012.tar
# Latest devkit
wget http://host.robots.ox.ac.uk/pascal/VOC/voc2012/VOCdevkit_18-May-2011.tar
"""
try:
    import Queue
except ImportError:
    import queue as Queue
import threading
import time
import glob
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import os
import itertools
import random


class VOCThread(threading.Thread):
    """Image Thread"""
    def __init__(self, queue, out_queue):
        threading.Thread.__init__(self)
        self.queue = queue
        self.out_queue = out_queue

    def run(self):
        while True:
            # Grabs image path from queue
            image_path_group, mask_path_group = self.queue.get()
            image_group = [plt.imread(i) for i in image_path_group]
            mask_group = [plt.imread(m) for m in mask_path_group]
            # Place images in out queue
            self.out_queue.put((image_group, mask_group))
            # Signals to queue job is done
            self.queue.task_done()


class VOC_dataset(object):
    def __init__(self, minibatch_size=3, which_set="train",voc_path="/data/lisa/data/PASCAL-VOC/VOCdevkit/"):
        image_paths = []
        mask_paths = []
        years = ["VOC2008", "VOC2009", "VOC2010", "VOC2011", "VOC2012"]
        for year in years:
            voc_year_path = os.path.join(voc_path, year)
            image_path = os.path.join(voc_year_path, "JPEGImages")
            more_image_paths = glob.glob(os.path.join(image_path, "*.jpg"))
            image_paths += more_image_paths
            mask_path = os.path.join(voc_year_path, "SegmentationClass")
            more_mask_paths = glob.glob(os.path.join(mask_path, "*.png"))
            mask_paths += more_mask_paths

        def match_paths(seg_file):
            names = []
            for year in years:
                voc_year_path = os.path.join(voc_path, year)
                fp = os.path.join(voc_year_path, "ImageSets", "Segmentation")
                with open(os.path.join(fp, seg_file)) as f:
                    names += [fi.strip() for fi in f.readlines()]

            ims = []
            masks = []
            s_ims = sorted(image_paths)
            s_masks = sorted(mask_paths)
            # Go through short list of names, find first match for each im and
            # mask and append
            for n in names:
                for i in s_ims:
                    if n in i:
                        ims.append(i)
                        break

                # slower but logic is easier
                for m in s_masks:
                    if n in m:
                        masks.append(m)
                        break
            assert len(ims) == len(masks)
            return ims, masks

        if which_set == "train":
            image_paths, mask_paths = match_paths("train.txt")
        elif which_set == "trainval":
            image_paths, mask_paths = match_paths("trainval.txt")
        else:
            raise ValueError("Unknown argument to which_set %s" % which_set)
        # no segmentations for the test set, assertion will fail
        #test_image_paths, test_mask_paths = match_paths("test.txt")
        self.image_paths = image_paths
        self.mask_paths = mask_paths

        assert len(self.image_paths) == len(self.mask_paths)

        self.n_per_epoch = len(image_paths)
        self.n_samples_seen_ = 0

        # Test random order
        # random.shuffle(self.image_paths)

        self.buffer_size = 5
        self.minibatch_size = minibatch_size
        self.input_qsize = 15
        self.min_input_qsize = 10
        if len(self.image_paths) % self.minibatch_size != 0:
            print("WARNING: Sample size not an even multiple of minibatch size")
            print("Truncating...")
            self.image_paths = self.image_paths[:-(
                len(self.image_paths) % self.minibatch_size)]
            self.mask_paths = self.mask_paths[:-(
                len(self.mask_paths) % self.minibatch_size)]

        assert len(self.image_paths) % self.minibatch_size == 0
        assert len(self.mask_paths) % self.minibatch_size == 0
        assert len(self.image_paths) == len(self.mask_paths)

        self.grouped_images = zip(*[iter(self.image_paths)] *
                                      self.minibatch_size)
        self.grouped_masks = zip(*[iter(self.mask_paths)] *
                                     self.minibatch_size)
        assert len(self.grouped_images) == len(self.grouped_masks)

        # Infinite...
        self.grouped_elements = itertools.cycle(zip(self.grouped_images,
                                                self.grouped_masks))
        self.queue = Queue.Queue()
        self.out_queue = Queue.Queue(maxsize=self.buffer_size)
        self._init_queues()

    def _init_queues(self):
        for i in range(1):
            self.it = VOCThread(self.queue, self.out_queue)
            self.it.setDaemon(True)
            self.it.start()

        # Populate queue with some paths to image data
        for n, _ in enumerate(range(self.input_qsize)):
            group = self.grouped_elements.next()
            self.queue.put(group)

    def __iter__(self):
        return self

    def __next__(self):
        return self.next()

    def next(self):
        return self._step()

    def reset(self):
        self.n_samples_seen_ = 0

    def _step(self):
        if self.n_samples_seen_ >= self.n_per_epoch:
            self.reset()
            raise StopIteration("End of epoch")
        image_group, mask_group = self.out_queue.get()
        self.n_samples_seen_ += self.minibatch_size
        if self.queue.qsize() <= self.min_input_qsize:
            for i in range(self.input_qsize):
                group = self.grouped_elements.next()
                self.queue.put(group)
        return image_group, mask_group


if __name__ == "__main__":
    # Example usage
    ds = VOC_dataset(which_set="trainval")
    start = time.time()
    #n_minibatches_to_run = 5000
    itr = 1
    while True:
        image_group, mask_group = ds.next()
        # time.sleep approximates running some model
        time.sleep(1)
        stop = time.time()
        tot = stop - start
        print("Threaded time: %s" % (tot))
        print("Minibatch %s" % str(itr))
        print("Time ratio (s per minibatch): %s" % (tot / float(itr)))
        itr += 1
        # test
        #if itr >= n_minibatches_to_run:
        #    break