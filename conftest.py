# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License
#

"""
Configuration file for Pytest

NOTE: DO NOT add fixtures here. It could generate problems with
      QtAwesome being called before a QApplication is created.
"""

import os
import os.path as osp
import re
import sys

# ---- To activate/deactivate certain things for pytest's only
# NOTE: Please leave this before any other import here!!
os.environ['SPYDER_PYTEST'] = 'True'

# ---- Pytest adjustments
import pytest


def pytest_addoption(parser):
    """Add option to run slow tests."""
    parser.addoption("--run-slow", action="store_true",
                     default=False, help="Run slow tests")


def get_passed_tests():
    """
    Get the list of passed tests by inspecting the log generated by pytest.

    This is useful on CIs to restart the test suite from the point where a
    segfault was thrown by it.
    """
    # This assumes the pytest log is placed next to this file. That's where
    # we put it on CIs.
    if osp.isfile('pytest_log.txt'):
        with open('pytest_log.txt') as f:
            logfile = f.readlines()

        # Detect all tests that passed before.
        test_re = re.compile(r'(spyder.*) [^ ]*(SKIPPED|PASSED|XFAIL)')
        tests = set()
        for line in logfile:
            match = test_re.match(line)
            if match:
                tests.add(match.group(1))

        return tests
    else:
        return []


def pytest_collection_modifyitems(config, items):
    """
    Decide what tests to run (slow or fast) according to the --run-slow
    option.
    """
    passed_tests = get_passed_tests()
    slow_option = config.getoption("--run-slow")
    skip_slow = pytest.mark.skip(reason="Need --run-slow option to run")
    skip_fast = pytest.mark.skip(reason="Don't need --run-slow option to run")
    skip_passed = pytest.mark.skip(reason="Test passed in previous runs")

    # Break test suite in CIs according to the following criteria:
    # * Mark all main window tests, and a percentage of the IPython console
    #   ones, as slow.
    # * All other tests will be considered as fast.
    # This provides a more balanced partitioning of our test suite (in terms of
    # necessary time to run it) between the slow and fast slots we have on CIs.
    slow_items = []
    if os.environ.get("CI") and not os.environ.get(
        "SPYDER_TEST_REMOTE_CLIENT"
    ):
        slow_items = [
            item for item in items if 'test_mainwindow' in item.nodeid
        ]

        ipyconsole_items = [
            item for item in items if 'test_ipythonconsole' in item.nodeid
        ]

        if os.name == 'nt':
            percentage = 0.4
        elif sys.platform == 'darwin':
            percentage = 0.3
        else:
            percentage = 0.3

        for i, item in enumerate(ipyconsole_items):
            if i < len(ipyconsole_items) * percentage:
                slow_items.append(item)

    for item in items:
        if slow_option:
            if item not in slow_items:
                item.add_marker(skip_fast)
        else:
            if item in slow_items:
                item.add_marker(skip_slow)

        if item.nodeid in passed_tests:
            item.add_marker(skip_passed)


@pytest.fixture(autouse=True)
def reset_conf_before_test(request):
    # To prevent running this fixture for a specific test, you need to use this
    # marker.
    if 'no_reset_conf' in request.keywords:
        return

    from spyder.config.manager import CONF
    CONF.reset_to_defaults(notification=False)

    from spyder.plugins.completion.api import COMPLETION_ENTRYPOINT
    from spyder.plugins.completion.plugin import CompletionPlugin

    # See compatibility note on `group` keyword:
    # https://docs.python.org/3/library/importlib.metadata.html#entry-points
    if sys.version_info < (3, 10):  # pragma: no cover
        from importlib_metadata import entry_points
    else:  # pragma: no cover
        from importlib.metadata import entry_points

    # Restore completion clients default settings, since they
    # don't have default values on the configuration.
    provider_configurations = {}
    for entry_point in entry_points(group=COMPLETION_ENTRYPOINT):
        Provider = entry_point.load()
        provider_name = Provider.COMPLETION_PROVIDER_NAME

        (provider_conf_version,
         current_conf_values,
         provider_defaults) = CompletionPlugin._merge_default_configurations(
            Provider, provider_name, provider_configurations)

        new_provider_config = {
            'version': provider_conf_version,
            'values': current_conf_values,
            'defaults': provider_defaults
        }
        provider_configurations[provider_name] = new_provider_config

    CONF.set('completions', 'provider_configuration', provider_configurations,
             notification=False)
