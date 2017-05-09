import json
from nose_parameterized import parameterized
import os
import sys
import unittest

import util


def is_valid_json(s):
    try:
        json.loads(s)
        return True
    except ValueError:
        return False


class AcceptanceTest(unittest.TestCase):

    @classmethod
    def setUpClass(self):
        test_directory = os.path.splitext(__file__)[0]
        testset_name = os.path.basename(test_directory)

        self.testset_work_dir = os.path.join(os.environ["ACCTEST_WORK_DIR"], testset_name)

        self.data_dir = os.path.join(os.environ["DATA_DIR"], testset_name)

        self.script = os.path.join(os.environ["BIN_DIR"], "dump_json.py")

    def work_dir(self, test_name):
        directory = os.path.join(self.testset_work_dir, test_name)
        util.maybe_create_dir(directory)
        return directory

    def test_usage(self):
        cmd = [self.script]
        util.run_cmd(self, cmd).expect_exit_code(2).expect_stderr(util.zeroth_line_starts_with(u"usage"))

    @parameterized.expand([
        [[]],
        [["--params"]],
        [["--no-params"]],
    ])
    def test_dump_to_stdout(self, options):
        model_file = os.path.join(self.data_dir, "model.pkl")
        self.assertTrue(os.path.exists(model_file))

        cmd = [self.script, model_file] + options
        util.run_cmd(self, cmd).expect_exit_code(0).expect_stdout(lambda o: is_valid_json('\n'.join(o)))

    @parameterized.expand([
        [[], "0"],
        [["--params"], "1"],
        [["--no-params"], "2"],
    ])
    def test_dump_to_a_file(self, options, subdir):
        test_work_dir = self.work_dir(os.path.join("test_dump_to_a_file", subdir))

        model_file = os.path.join(self.data_dir, "model.pkl")
        self.assertTrue(os.path.exists(model_file))

        output_file = os.path.join(test_work_dir, "output.json")
        open(output_file, "w").close()

        cmd = [self.script, model_file, "--out_file", output_file] + options
        error_message = "RuntimeError: File/path for 'out_file' exists, {}".format(output_file)
        util.run_cmd(self, cmd).expect_exit_code(1).expect_stderr(util.last_line_starts_with(error_message))

        os.remove(output_file)

        info_message = "Writing to file:  {}".format(output_file)
        util.run_cmd(self, cmd).expect_exit_code(0).expect_stdout(lambda o: o == [info_message])

        self.assertTrue(os.path.exists(output_file))
        dump = open(output_file, 'r').read()

        self.assertTrue(is_valid_json(dump))
