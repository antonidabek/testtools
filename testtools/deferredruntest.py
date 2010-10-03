# Copyright (c) 2010 Jonathan M. Lange. See LICENSE for details.

"""Individual test case execution for tests that return Deferreds."""

__all__ = [
    'AsynchronousDeferredRunTest',
    'SynchronousDeferredRunTest',
    ]

from testtools.runtest import RunTest

from twisted.internet import defer
from twisted.python.failure import Failure
from twisted.python.util import mergeFunctionMetadata
from twisted.trial.unittest import TestCase


# XXX: Copied & pasted from somewhere else.  No tests in testtools.
def extract_result(deferred):
    """Extract the result from a fired deferred.

    It can happen that you have an API that returns Deferreds for
    compatibility with Twisted code, but is in fact synchronous, i.e. the
    Deferreds it returns have always fired by the time it returns.  In this
    case, you can use this function to convert the result back into the usual
    form for a synchronous API, i.e. the result itself or a raised exception.

    It would be very bad form to use this as some way of checking if a
    Deferred has fired.
    """
    failures = []
    successes = []
    deferred.addCallbacks(successes.append, failures.append)
    if len(failures) == 1:
        failures[0].raiseException()
    elif len(successes) == 1:
        return successes[0]
    else:
        # XXX: AssertionError might be inappropriate.  Perhaps we should have
        # a custom error message.
        raise AssertionError("%r has not fired yet." % (deferred,))


class SynchronousDeferredRunTest(RunTest):

    def _run_user(self, function, *args):
        d = defer.maybeDeferred(function, *args)
        def got_exception(failure):
            e = failure.value
            for exc_class, handler in self.handlers:
                if isinstance(e, exc_class):
                    self._exceptions.append(e)
                    return self.exception_caught
            return failure
        d.addErrback(got_exception)
        # XXX: Cheat!
        result = extract_result(d)
        return result


class AsynchronousDeferredRunTest(RunTest):

    def _run_user(self, function, *args):
        trial = TestCase()
        d = defer.maybeDeferred(function, *args)
        trial._wait(d)


class ReentryError(Exception):
    """Raised when we try to re-enter a function that forbids it."""

    def __init__(self, function):
        super(ReentryError, self).__init__(
            "%r in not re-entrant but was called within a call to itself."
            % (function,))


def not_reentrant(function, _calls={}):
    """Decorates a function as not being re-entrant.

    The decorated function will raise an error if called from within itself.
    """
    def decorated(*args, **kwargs):
        if _calls.get(function, False):
            raise ReentryError(function)
        _calls[function] = True
        try:
            return function(*args, **kwargs)
        finally:
            _calls[function] = False
    return mergeFunctionMetadata(function, decorated)


class TimeoutError(Exception):
    """Raised when run_in_reactor takes too long to run a function."""


class _Spinner(object):

    _UNSET = object()

    def __init__(self, reactor):
        self._reactor = reactor
        self._timeout_call = None
        self._success = self._UNSET
        self._failure = self._UNSET

    def _cancel_timeout(self):
        if self._timeout_call:
            self._timeout_call.cancel()

    def _get_result(self):
        if self._failure is not self._UNSET:
            self._failure.raiseException()
        if self._success is not self._UNSET:
            return self._success
        raise AssertionError("Tried to get result when no result is available.")

    def _got_failure(self, result):
        self._cancel_timeout()
        self._failure = result

    def _got_success(self, result):
        self._cancel_timeout()
        self._success = result

    def _stop_reactor(self, ignored=None):
        """Stop the reactor!"""
        self._reactor.crash()

    def _timed_out(self, function, timeout):
        e = TimeoutError(
            "%r took longer than %s seconds" % (function, timeout))
        self._failure = Failure(e)
        self._stop_reactor()

    @not_reentrant
    def run(self, timeout, function, *args, **kwargs):
        """Run 'function' in a reactor.

        If 'function' returns a Deferred, the reactor will keep spinning until
        the Deferred fires and its chain completes or until the timeout is
        reached -- whichever comes first.
        """
        self._timeout_call = self._reactor.callLater(
            timeout, self._timed_out, function, timeout)
        def run_function():
            d = defer.maybeDeferred(function, *args, **kwargs)
            d.addCallbacks(self._got_success, self._got_failure)
            d.addBoth(self._stop_reactor)
        self._reactor.callWhenRunning(run_function)
        self._reactor.run()
        return self._get_result()
