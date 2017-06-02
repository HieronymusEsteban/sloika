"""Tests for fileio module"""
import os
import unittest
from sloika import fileio


class FileIOTest(unittest.TestCase):

    @classmethod
    def setUpClass(self):
        data_dir = os.environ['DATA_DIR']
        self.strand1 = os.path.join(data_dir, 'strands.txt')
        self.strand2 = os.path.join(data_dir, 'strands_single.txt')
        self.filenames = [b'read03.fast5', b'read16.fast5', b'read27.fast5']

    def test_read_strand_list_with_multiple_strands(self):
        strand_list = fileio.readtsv(self.strand1)
        self.assertTrue('filename' in strand_list.dtype.names)
        self.assertEqual(len(strand_list), 3)
        self.assertTrue(all(strand_list['filename'] == self.filenames))

    def test_read_strand_list_with_single_strands(self):
        strand_list = fileio.readtsv(self.strand2)
        self.assertTrue('filename' in strand_list.dtype.names)
        self.assertEqual(len(strand_list), 1)
        self.assertEqual(strand_list['filename'], self.filenames[0])
