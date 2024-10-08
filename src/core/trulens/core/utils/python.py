"""Utilities related to core python functionalities."""

from __future__ import annotations

import asyncio
from concurrent import futures
from contextlib import asynccontextmanager
from contextlib import contextmanager
import contextvars
import dataclasses
import inspect
import logging
from pprint import PrettyPrinter
import queue
import sys
from types import FrameType
from types import ModuleType
import typing
from typing import (
    Any,
    AsyncGenerator,
    Awaitable,
    Callable,
    Dict,
    Generator,
    Generic,
    Hashable,
    Iterable,
    Iterator,
    List,
    Optional,
    Sequence,
    Type,
    TypeVar,
    Union,
)
import weakref

T = TypeVar("T")

WRAP_LAZY: bool = True

Thunk = Callable[[], T]
"""A function that takes no arguments."""

if sys.version_info >= (3, 11):
    getmembers_static = inspect.getmembers_static
else:

    def getmembers_static(obj, predicate=None):
        """Implementation of inspect.getmembers_static for python < 3.11."""

        if predicate is None:
            predicate = lambda name, value: True

        return [
            (name, value)
            for name in dir(obj)
            if hasattr(obj, name)
            and predicate(name, value := getattr(obj, name))
        ]


if sys.version_info >= (3, 9):
    Future = futures.Future
    """Alias for [concurrent.futures.Future][].

    In python < 3.9, a subclass of [concurrent.futures.Future][] with
    `Generic[A]` is used instead.
    """

    Queue = queue.Queue
    """Alias for [queue.Queue][] .

    In python < 3.9, a subclass of [queue.Queue][] with
    `Generic[A]` is used instead.
    """

else:
    # Fake classes which can have type args. In python earlier than 3.9, the
    # classes imported above cannot have type args which is annoying for type
    # annotations. We use these fake ones instead.

    A = TypeVar("A")

    # HACK011
    class Future(Generic[A], futures.Future):
        """Alias for [concurrent.futures.Future][].

        In python < 3.9, a subclass of [concurrent.futures.Future][] with
        `Generic[A]` is used instead.
        """

    # HACK012
    class Queue(Generic[A], queue.Queue):
        """Alias for [queue.Queue][] .

        In python < 3.9, a subclass of [queue.Queue][] with
        `Generic[A]` is used instead.
        """


class EmptyType(type):
    """A type that cannot be instantiated or subclassed."""

    def __new__(mcs, *args, **kwargs):
        raise ValueError("EmptyType cannot be instantiated.")

    def __instancecheck__(cls, __instance: Any) -> bool:
        return False

    def __subclasscheck__(cls, __subclass: Type) -> bool:
        return False


if sys.version_info >= (3, 10):
    import types

    NoneType = types.NoneType
    """Alias for [types.NoneType][] .

    In python < 3.10, it is defined as `type(None)` instead.
    """

else:
    NoneType = type(None)
    """Alias for [types.NoneType][] .

    In python < 3.10, it is defined as `type(None)` instead.
    """

logger = logging.getLogger(__name__)
pp = PrettyPrinter()

# Reflection utilities.


def class_name(obj: Union[Type, Any]) -> str:
    """Get the class name of the given object or instance."""

    if hasattr(obj, "__name__"):
        return obj.__name__

    if hasattr(obj, "__class__"):
        return obj.__class__.__name__

    return str(obj)


def module_name(obj: Union[ModuleType, Type, Any]) -> str:
    """Get the module name of the given module, class, or instance."""

    if isinstance(obj, ModuleType):
        return obj.__name__

    if hasattr(obj, "__module__"):
        return obj.__module__  # already a string name

    return "unknown module"


def callable_name(c: Callable):
    """Get the name of the given callable."""

    if isinstance(c, staticmethod):
        return callable_name(c.__func__)

    if isinstance(c, classmethod):
        return callable_name(c.__func__)

    if not isinstance(c, Callable):
        raise ValueError(
            f"Expected a callable. Got {class_name(type(c))} instead."
        )

    if safe_hasattr(c, "__name__"):
        return c.__name__

    if safe_hasattr(c, "__call__"):
        return callable_name(c.__call__)

    return str(c)


def id_str(obj: Any) -> str:
    """Get the id of the given object as a string in hex."""

    return f"0x{id(obj):x}"


def is_really_coroutinefunction(func) -> bool:
    """Determine whether the given function is a coroutine function.

    Warning:
        Inspect checkers for async functions do not work on openai clients,
        perhaps because they use `@typing.overload`. Because of that, we detect
        them by checking `__wrapped__` attribute instead. Note that the inspect
        docs suggest they should be able to handle wrapped functions but perhaps
        they handle different type of wrapping? See
        https://docs.python.org/3/library/inspect.html#inspect.iscoroutinefunction
        . Another place they do not work is the decorator langchain uses to mark
        deprecated functions.
    """

    if inspect.iscoroutinefunction(func):
        return True

    if hasattr(func, "__wrapped__") and inspect.iscoroutinefunction(
        func.__wrapped__
    ):
        return True

    return False


def safe_signature(func_or_obj: Any):
    """Get the signature of the given function.

    Sometimes signature fails for wrapped callables and in those cases we check
    for `__call__` attribute and use that instead.
    """
    try:
        assert isinstance(
            func_or_obj, Callable
        ), f"Expected a Callable. Got {type(func_or_obj)} instead."

        return inspect.signature(func_or_obj)

    except Exception as e:
        if safe_hasattr(func_or_obj, "__call__"):
            # If given an obj that is callable (has __call__ defined), we want to
            # return signature of that call instead of letting inspect.signature
            # explore that object further. Doing so may produce exceptions due to
            # contents of those objects producing exceptions when attempting to
            # retrieve them.

            return inspect.signature(func_or_obj.__call__)

        else:
            raise e


def safe_hasattr(obj: Any, k: str) -> bool:
    """Check if the given object has the given attribute.

    Attempts to use static checks (see [inspect.getattr_static][]) to avoid any
    side effects of attribute access (i.e. for properties).
    """
    try:
        v = inspect.getattr_static(obj, k)
    except AttributeError:
        return False

    is_prop = False
    try:
        # OpenAI version 1 classes may cause this isinstance test to raise an
        # exception.
        is_prop = isinstance(v, property)
    except Exception:
        return False

    if is_prop:
        try:
            v.fget(obj)
            return True
        except Exception:
            return False
    else:
        return True


def safe_issubclass(cls: Type, parent: Type) -> bool:
    """Check if the given class is a subclass of the given parent class."""

    origin = typing.get_origin(cls)
    if origin is None:
        return issubclass(cls, parent)

    return issubclass(origin, parent)


# Function utilities.


def code_line(func, show_source: bool = False) -> Optional[str]:
    """Get a string representation of the location of the given function
    `func`."""

    if isinstance(func, inspect.FrameInfo):
        ret = f"{func.filename}:{func.lineno}"
        if show_source and func.code_context is not None:
            ret += "\n"
            for line in func.code_context:
                ret += "\t" + line

        return ret

    if inspect.isframe(func):
        code = func.f_code
        ret = f"{func.f_code.co_filename}:{func.f_code.co_firstlineno}"

    elif safe_hasattr(func, "__code__"):
        code = func.__code__
        ret = f"{code.co_filename}:{code.co_firstlineno}"

    else:
        return None

    if show_source:
        ret += "\n"
        for line in inspect.getsourcelines(func)[0]:
            ret += "\t" + str(line)

    return ret


def locals_except(*exceptions):
    """
    Get caller's locals except for the named exceptions.
    """

    locs = caller_frame(offset=1).f_locals  # 1 to skip this call

    return {k: v for k, v in locs.items() if k not in exceptions}


def for_all_methods(decorator, _except: Optional[List[str]] = None):
    """
    Applies decorator to all methods except classmethods, private methods and
    the ones specified with `_except`.
    """

    def decorate(cls):
        for (
            attr_name,
            attr,
        ) in cls.__dict__.items():  # does not include classmethods
            if not inspect.isfunction(attr):
                continue  # skips non-method attributes

            if attr_name.startswith("_"):
                continue  # skips private methods

            if _except is not None and attr_name in _except:
                continue

            logger.debug("Decorating %s", attr_name)
            setattr(cls, attr_name, decorator(attr))

        return cls

    return decorate


def run_before(callback: Callable):
    """
    Create decorator to run the callback before the function.
    """

    def decorator(func):
        def wrapper(*args, **kwargs):
            callback(*args, **kwargs)
            return func(*args, **kwargs)

        return wrapper

    return decorator


# Python call stack utilities

# Attribute name for storing a callstack in asyncio tasks.
STACK = "__tru_stack"


def superstack() -> Iterator[FrameType]:
    """Get the current stack (not including this function) with frames reaching
    across Tasks and threads.
    """

    frames = stack_with_tasks()
    next(iter(frames))  # skip this method itself
    # NOTE: skipping offset frames is done below since the full stack may need
    # to be reconstructed there.

    # Using queue for frames as additional frames may be added due to handling threads.
    q = queue.Queue()
    for f in frames:
        q.put(f)

    while not q.empty():
        f = q.get()
        yield f

        if id(f.f_code) == id(_future_target_wrapper.__code__):
            locs = f.f_locals

            assert (
                "pre_start_stack" in locs
            ), "Pre thread start stack expected but not found."
            for fi in locs["pre_start_stack"].get():  # is WeakWrapper
                q.put(fi.frame)

            continue

    return


def caller_module_name(offset=0) -> str:
    """
    Get the caller's (of this function) module name.
    """
    frame = caller_frame(offset=offset + 1)
    return frame.f_globals["__name__"]


def caller_module(offset=0) -> ModuleType:
    """
    Get the caller's (of this function) module.
    """

    return sys.modules[caller_module_name(offset=offset + 1)]


def caller_frame(offset=0) -> FrameType:
    """
    Get the caller's (of this function) frame. See
    https://docs.python.org/3/reference/datamodel.html#frame-objects .
    """
    caller_frame = inspect.currentframe()
    for _ in range(offset + 1):
        if caller_frame is None:
            raise RuntimeError("No current frame found.")
        caller_frame = caller_frame.f_back

    if caller_frame is None:
        raise RuntimeError("No caller frame found.")

    return caller_frame


def external_caller_frame(offset=0) -> FrameType:
    """Get the caller's (of this function) frame that is not in the trulens
    namespace.

    Raises:
        RuntimeError: If no such frame is found.
    """
    frame = inspect.currentframe()
    gen = stack_generator(frame=frame, offset=offset + 2)
    for f_info in gen:
        if not f_info.f_globals["__name__"].startswith("trulens"):
            return f_info

    raise RuntimeError("No external caller frame found.")


def caller_frameinfo(
    offset: int = 0, skip_module: Optional[str] = "trulens"
) -> Optional[inspect.FrameInfo]:
    """
    Get the caller's (of this function) frameinfo. See
    https://docs.python.org/3/reference/datamodel.html#frame-objects .

    Args:
        offset: The number of frames to skip. Default is 0.

        skip_module: Skip frames from the given module. Default is "trulens".
    """

    for f_info in inspect.stack(0)[offset + 1 :]:
        if skip_module is None:
            return f_info
        if not f_info.frame.f_globals["__name__"].startswith(skip_module):
            return f_info

    return None


def task_factory_with_stack(loop, coro, *args, **kwargs) -> asyncio.Task:
    """A task factory that annotates created tasks with stacks of their parents.

    All of such annotated stacks can be retrieved with
    [stack_with_tasks][trulens.core.utils.python.stack_with_tasks] as one merged
    stack.
    """

    if "context" not in kwargs:
        kwargs["context"] = contextvars.copy_context()

    parent_task = asyncio.current_task(loop=loop)
    task = asyncio.tasks.Task(coro=coro, loop=loop, *args, **kwargs)

    frame = inspect.currentframe()
    stack = stack_generator(frame=frame, offset=3)

    if parent_task is not None:
        stack = merge_stacks(stack, parent_task.get_stack()[::-1])
        # skipping create_task and task_factory

    setattr(task, STACK, stack)

    return task


# If there is already a loop running, try to patch its task factory.
try:
    loop = asyncio.get_running_loop()
    loop.set_task_factory(task_factory_with_stack)
except Exception:
    pass

# Instrument new_event_loop to set the above task_factory upon creation:
original_new_event_loop = asyncio.new_event_loop


def tru_new_event_loop():
    """Replacement for [new_event_loop][asyncio.new_event_loop] that sets
    the task factory to make tasks that copy the stack from their creators."""

    loop = original_new_event_loop()
    loop.set_task_factory(task_factory_with_stack)
    return loop


asyncio.new_event_loop = tru_new_event_loop


def get_task_stack(task: asyncio.Task) -> Sequence[FrameType]:
    """Get the annotated stack (if available) on the given task."""

    if safe_hasattr(task, STACK):
        return getattr(task, STACK)
    else:
        # get_stack order is reverse of inspect.stack:
        return task.get_stack()[::-1]


def merge_stacks(
    s1: Iterable[FrameType], s2: Sequence[FrameType]
) -> Sequence[FrameType]:
    """
    Assuming `s1` is a subset of `s2`, combine the two stacks in presumed call
    order.
    """

    ret = []

    for f in s1:
        ret.append(f)
        try:
            s2i = s2.index(f)
            for _ in range(s2i):
                ret.append(s2[0])
                s2 = s2[1:]
        except Exception:
            pass

    return ret


def stack_generator(
    frame: Optional[FrameType] = None, offset: int = 0
) -> Iterable[FrameType]:
    if frame is None:
        frame = inspect.currentframe()
    for _ in range(offset):
        if frame is None:
            raise ValueError("No frame found.")
        frame = frame.f_back
    while frame is not None:
        yield frame
        frame = frame.f_back


def stack_with_tasks() -> Iterable[FrameType]:
    """Get the current stack (not including this function) with frames reaching
    across Tasks.
    """
    frame = inspect.currentframe()
    frame_gen = stack_generator(frame=frame, offset=1)
    try:
        task_stack = get_task_stack(asyncio.current_task())

        return merge_stacks(frame_gen, task_stack)

    except Exception:
        return frame_gen


class _Wrap(Generic[T]):
    """Wrap an object.

    See WeakWrapper for explanation.
    """

    def __init__(self, obj: T):
        self.obj: T = obj

    def get(self) -> T:
        return self.obj


@dataclasses.dataclass
class WeakWrapper(Generic[T]):
    """Wrap an object with a weak reference.

    This is to be able to use weakref.ref on objects like lists which are
    otherwise not weakly referenceable. The goal of this class is to generalize
    weakref.ref to work with any object."""

    obj: weakref.ReferenceType[Union[_Wrap[T], T]]

    def __init__(self, obj: Union[weakref.ReferenceType[T], WeakWrapper[T], T]):
        if isinstance(obj, weakref.ReferenceType):
            self.obj = obj

        else:
            if isinstance(obj, WeakWrapper):
                obj = obj.get()

            try:
                # Try to make reference to obj directly.
                self.obj = weakref.ref(obj)

            except Exception:
                # If its a list or other non-weakly referenceable object, wrap it.
                self.obj = weakref.ref(_Wrap(obj))

    def get(self) -> T:
        """Get the wrapped object."""

        temp = self.obj()  # undo weakref.ref
        if isinstance(temp, _Wrap):
            return temp.get()  # undo _Wrap if needed
        else:
            return temp


def _future_target_wrapper(stack, func, *args, **kwargs):
    """Wrapper for a function that is started by threads.

    This is needed to record the call stack prior to thread creation as in
    python threads do not inherit the stack. Our instrumentation, however,
    relies on walking the stack and need to do this to the frames prior to
    thread starts.
    """

    # Keep this for looking up via get_first_local_in_call_stack .
    pre_start_stack = WeakWrapper(stack)  # noqa: F841 # pylint: disable=W0612

    # with with_context(context):
    return func(*args, **kwargs)


def get_all_local_in_call_stack(
    key: str,
    func: Callable[[Callable], bool],
    offset: Optional[int] = 1,
    skip: Optional[Any] = None,  # really frame
) -> Iterator[Any]:
    """Find locals in call stack by name.

    Args:
        key: The name of the local variable to look for.

        func: Recognizer of the function to find in the call stack.

        offset: The number of top frames to skip.

        skip: A frame to skip as well.

    Note:
        `offset` is unreliable for skipping the intended frame when operating
        with async tasks. In those cases, the `skip` argument is more reliable.

    Returns:
        An iterator over the values of the local variable named `key` in the
            stack at all of the frames executing a function which `func` recognizes
            (returns True on) starting from the top of the stack except `offset` top
            frames.

            Returns None if `func` does not recognize any function in the stack.

    Raises:
        RuntimeError: Raised if a function is recognized but does not have `key`
            in its locals.

    This method works across threads as long as they are started using
    [TP][trulens.core.utils.threading.TP].
    """

    frames_gen = stack_with_tasks()
    # NOTE: skipping offset frames is done below since the full stack may need
    # to be reconstructed there.

    # Using queue for frames as additional frames may be added due to handling threads.
    q = queue.Queue()
    for i, f in enumerate(frames_gen):
        if i == 0:
            # skip this method itself
            continue
        q.put(f)

    while not q.empty():
        f = q.get()

        if id(f.f_code) == id(_future_target_wrapper.__code__):
            locs = f.f_locals
            assert (
                "pre_start_stack" in locs
            ), "Pre thread start stack expected but not found."
            for fi in locs["pre_start_stack"].get():  # is WeakWrapper
                q.put(fi.frame)

            continue

        if offset is not None and offset > 0:
            offset -= 1
            continue

        if func(f.f_code):
            logger.debug(
                "Looking via %s; found %s", callable_name(func), str(f)
            )
            if skip is not None and f == skip:
                logger.debug("Skipping.")
                continue

            locs = f.f_locals
            if key in locs:
                yield locs[key]
            else:
                raise KeyError(f"No local named '{key}' found in frame {f}.")

    return


def get_first_local_in_call_stack(
    key: str,
    func: Callable[[Callable], bool],
    offset: Optional[int] = 1,
    skip: Optional[Any] = None,  # actually frame
) -> Optional[Any]:
    """
    Get the value of the local variable named `key` in the stack at the nearest
    frame executing a function which `func` recognizes (returns True on)
    starting from the top of the stack except `offset` top frames. If `skip`
    frame is provided, it is skipped as well. Returns None if `func` does not
    recognize the correct function. Raises RuntimeError if a function is
    recognized but does not have `key` in its locals.

    This method works across threads as long as they are started using the TP
    class above.

    NOTE: `offset` is unreliable for skipping the intended frame when operating
    with async tasks. In those cases, the `skip` argument is more reliable.
    """

    try:
        return next(
            iter(
                get_all_local_in_call_stack(
                    key, func, offset=offset + 1, skip=skip
                )
            )
        )
    except StopIteration:
        logger.debug("no frames found")
        return None


# Wrapping utilities


class OpaqueWrapper(Generic[T]):
    """Wrap an object preventing all access.

    Any access except to
    [unwrap][trulens.core.utils.python.OpaqueWrapper.unwrap] will result in an
    exception with the given message.

    Args:
        obj: The object to wrap.

        e: The exception to raise when an attribute is accessed.
    """

    def __init__(self, obj: T, e: Exception):
        self._obj = obj
        self._e = e

    def unwrap(self) -> T:
        """Get the wrapped object back."""
        return self._obj

    def __getattr__(self, name):
        raise self._e

    def __setattr__(self, name, value):
        if name in ["_obj", "_e"]:
            return super().__setattr__(name, value)
        raise self._e

    def __call__(self, *args: Any, **kwds: Any) -> Any:
        raise self._e


ContextVarsOrValues = Union[
    Iterable[contextvars.ContextVar],
    contextvars.Context,
    Dict[contextvars.ContextVar, Any],
]


def set_context_vars_or_values(
    context_vars: Optional[ContextVarsOrValues] = None,
) -> Dict[contextvars.ContextVar, contextvars.Token]:
    """Get the tokens for the given context variables or values.

    Args:
        context_vars: The context variables or values to get tokens for.

    Returns:
        A dictionary of context variables to tokens.
    """

    if context_vars is None:
        return {}

    if isinstance(context_vars, (dict, contextvars.Context)):
        return {cv: cv.set(v) for cv, v in context_vars.items()}

    return {cv: cv.set(cv.get()) for cv in context_vars}


@contextmanager
def with_context(context_vars: Optional[ContextVarsOrValues] = None):
    """Context manager to set context variables to given values.

    Args:
        context_vars: The context variables to set. If a dictionary is given,
            the keys are the context variables and the values are the values to
            set them to. If an iterable is given, it should be a list of context
            variables to set to their current value.
    """

    tokens = set_context_vars_or_values(context_vars)

    try:
        yield

    finally:
        for cv, v in tokens.items():
            try:
                cv.reset(v)
            except Exception:
                # TODO: Figure out if this is bad.
                pass


@asynccontextmanager
async def awith_context(context_vars: Optional[ContextVarsOrValues] = None):
    """Context manager to set context variables to given values.

    Args:
        context_vars: The context variables to set. If a dictionary is given,
            the keys are the context variables and the values are the values to
            set them to. If an iterable is given, it should be a list of context
            variables to set to their current value.
    """

    tokens = set_context_vars_or_values(context_vars)

    try:
        yield

    finally:
        for cv, v in tokens.items():
            try:
                cv.reset(v)
            except Exception:
                # TODO: Figure out if this is bad.
                pass


def wrap_awaitable(
    awaitable: Awaitable[T],
    on_await: Optional[Callable[[], Any]] = None,
    wrap: Optional[Callable[[T], T]] = None,
    on_done: Optional[Callable[[T], T]] = None,
    context_vars: Optional[ContextVarsOrValues] = None,
) -> Awaitable[T]:
    """Wrap an awaitable in another awaitable that will call callbacks before
    and after the given awaitable finishes.

    !!! Important
        This method captures a [Context][contextvars.Context] at the time this
        method is called and copies it over to the wrapped awaitable.

    Note that the resulting awaitable needs to be awaited for the callback to
    eventually trigger.

    Args:
        awaitable: The awaitable to wrap.

        on_await: The callback to call when the wrapper awaitable is awaited but
            before the wrapped awaitable is awaited.

        wrap: The callback to call with the result of the wrapped awaitable
            once it is ready. This should return the value or a wrapped version.

        on_done: For compatibility with generators, this is called after wrap.

        context_vars: The context variables to copy over to the wrapped
            awaitable. If None, all context variables are copied. See
            [with_context][trulens.core.utils.python.with_context].
    """

    async def wrapper(awaitable):
        async with awith_context(context_vars):
            if on_await is not None:
                on_await()

            val = await awaitable

            if wrap is not None:
                val = wrap(val)  # allow handlers to transform the value

            if on_done is not None:
                val = on_done(val)

            return val

    wrapper.__name__ = f"tru_wrapped_{awaitable.__class__.__name__}"

    return wrapper(awaitable)


def wrap_generator(
    gen: Generator[T, None, None],
    on_iter: Optional[Callable[[], Any]] = None,
    wrap: Optional[Callable[[T], T]] = None,
    on_done: Optional[Callable[[List[T]], Any]] = None,
    context_vars: Optional[ContextVarsOrValues] = None,
) -> Generator[T, None, None]:
    """Wrap a generator in another generator that will call callbacks at various
    points in the generation process.

    Args:
        gen: The generator to wrap.

        on_iter: The callback to call when the wrapper generator is created but
            before a first iteration is produced.

        wrap: The callback to call with the result of each iteration of the
            wrapped generator. This should return the value or a wrapped
            version.

        on_done: The callback to call when the wrapped generator is exhausted.

        context_vars: The context variables to copy over to the wrapped
            generator. If None, all context variables are taken with their
            present values. See
            [with_context][trulens.core.utils.python.with_context].
    """

    def wrapper(gen):
        with with_context(context_vars):
            if on_iter is not None:
                on_iter()

            vals = []

            for val in gen:
                if wrap is not None:
                    # Allow handlers to transform the value.
                    val = wrap(val)

                vals.append(val)
                yield val

            if on_done is not None:
                on_done(vals)

    wrapper.__name__ = f"tru_wrapped_{gen.__class__.__name__}"

    return wrapper(gen)


def wrap_async_generator(
    gen: AsyncGenerator[T, None],
    on_iter: Optional[Callable[[], Any]] = None,
    wrap: Optional[Callable[[T], T]] = None,
    on_done: Optional[Callable[[List[T]], Any]] = None,
    context_vars: Optional[ContextVarsOrValues] = None,
) -> AsyncGenerator[T, None]:
    """Wrap a generator in another generator that will call callbacks at various
    points in the generation process.

    Args:
        gen: The generator to wrap.

        on_iter: The callback to call when the wrapper generator is created but
            before a first iteration is produced.

        wrap: The callback to call with the result of each iteration of the
            wrapped generator.

        on_done: The callback to call when the wrapped generator is exhausted.

        context_vars: The context variables to copy over to the wrapped
            generator. If None, all context variables are taken with their
            present values. See
            [with_context][trulens.core.utils.python.with_context].
    """

    async def wrapper(gen):
        with with_context(context_vars):
            if on_iter is not None:
                on_iter()

            vals = []

            async for val in gen:
                if wrap is not None:
                    val = wrap(val)  # allow handlers to rewrap the value

                vals.append(val)

                yield val

            if on_done is not None:
                on_done(vals)

    wrapper.__name__ = f"tru_wrapped_{gen.__class__.__name__}"

    return wrapper(gen)


def is_lazy(obj):
    """Check if the given object is lazy.

    An object is considered lazy if it is a generator or an awaitable.
    """

    return (
        inspect.isawaitable(obj)
        or inspect.isgenerator(obj)
        or inspect.isasyncgen(obj)
    )


def wrap_lazy(
    obj: Any,
    on_start: Optional[Callable[[], None]] = None,
    wrap: Optional[Callable[[T], T]] = None,
    on_done: Optional[Callable[[Any], Any]] = None,  # Any may be T or List[T]
    context_vars: Optional[ContextVarsOrValues] = None,
) -> Any:
    """Wrap a lazy value in one that will call
    callbacks at various points in the generation process.

    Args:
        gen: The lazy value.

        on_start: The callback to call when the wrapper is created.

        wrap: The callback to call with the result of each iteration of the
            wrapped generator or the result of an awaitable. This should return
            the value or a wrapped version.

        on_done: The callback to call when the wrapped generator is exhausted or
            awaitable is ready.

        context_vars: The context variables to copy over to the wrapped
            generator. If None, all context variables are taken with their
            present values. See
            [with_context][trulens.core.utils.python.with_context].
    """

    if not WRAP_LAZY:
        return obj

    if inspect.isasyncgen(obj):
        return wrap_async_generator(
            obj,
            on_iter=on_start,
            wrap=wrap,
            on_done=on_done,
            context_vars=context_vars,
        )

    if inspect.isgenerator(obj):
        return wrap_generator(
            obj,
            on_iter=on_start,
            wrap=wrap,
            on_done=on_done,
            context_vars=context_vars,
        )

    if inspect.isawaitable(obj):
        return wrap_awaitable(
            obj,
            on_await=on_start,
            wrap=wrap,
            on_done=on_done,
            context_vars=context_vars,
        )

    raise ValueError(f"Object of type {type(obj)} is not lazy.")


def wrap_until_eager(
    obj,
    on_eager: Optional[Callable[[Any], T]] = None,
    context_vars: Optional[ContextVarsOrValues] = None,
) -> T | Sequence[T]:
    """Wrap a lazy value in one that will call callbacks one the final non-lazy
    values.

    Arts:
        obj: The lazy value.

        on_eager: The callback to call with the final value of the wrapped
            generator or the result of an awaitable. This should return the
            value or a wrapped version.

        context_vars: The context variables to copy over to the wrapped
            generator. If None, all context variables are taken with their
            present values. See
            [with_context][trulens.core.utils.python.with_context].
    """

    def rewrap(obj_):
        if is_lazy(obj_):
            return wrap_lazy(
                obj_, wrap=rewrap, on_done=rewrap, context_vars=context_vars
            )

        if on_eager is not None:
            return on_eager(obj_)

        return obj_

    return rewrap(obj)


# Class utilities


@dataclasses.dataclass
class SingletonInfo(Generic[T]):
    """
    Information about a singleton instance.
    """

    val: T
    """The singleton instance."""

    cls: Type[T]
    """The class of the singleton instance."""

    frameinfo_codeline: Optional[str]
    """The frame where the singleton was created.

    This is used for showing "already created" warnings. This is intentionally
    not the frame itself but a rendering of it to avoid maintaining references
    to frames and all of the things a frame holds onto.
    """

    name: Optional[str] = None
    """The name of the singleton instance.

    This is used for the SingletonPerName mechanism to have a separate singleton
    for each unique name (and class).
    """

    def __init__(self, name: str, val: Any):
        self.val = val
        self.cls = val.__class__
        self.name = name
        self.frameinfo_codeline = code_line(
            caller_frameinfo(offset=2), show_source=True
        )

    def warning(self):
        """Issue warning that this singleton already exists."""

        logger.warning(
            (
                "Singleton instance of type %s already created at:\n%s\n"
                "You can delete the singleton by calling `<instance>.delete_singleton()` or \n"
                f"""  ```python
  from trulens.core.utils.python import SingletonPerName
  SingletonPerName.delete_singleton_by_name(name="{self.name}", cls={self.cls.__name__})
  ```
            """
            ),
            self.cls.__name__,
            self.frameinfo_codeline,
        )


class SingletonPerName:
    """
    Class for creating singleton instances except there being one instance max,
    there is one max per different `name` argument. If `name` is never given,
    reverts to normal singleton behavior.
    """

    # Hold singleton instances here.
    _instances: Dict[Hashable, SingletonInfo[SingletonPerName]] = {}

    # Need some way to look up the name of the singleton instance. Cannot attach
    # a new attribute to instance since some metaclasses don't allow this (like
    # pydantic). We instead create a map from instance address to name.
    _id_to_name_map: Dict[int, Optional[str]] = {}

    def warning(self):
        """Issue warning that this singleton already exists."""

        name = SingletonPerName._id_to_name_map[id(self)]
        k = self.__class__.__name__, name
        if k in SingletonPerName._instances:
            SingletonPerName._instances[k].warning()
        else:
            raise RuntimeError(
                f"Instance of singleton type/name {k} does not exist."
            )

    def __new__(
        cls: Type[SingletonPerName],
        *args,
        name: Optional[str] = None,
        **kwargs,
    ) -> SingletonPerName:
        """
        Create the singleton instance if it doesn't already exist and return it.
        """

        k = cls.__name__, name

        if k not in cls._instances:
            logger.debug(
                "*** Creating new %s singleton instance for name = %s ***",
                cls.__name__,
                name,
            )
            # If exception happens here, the instance should not be added to
            # _instances.
            instance = super().__new__(cls)

            SingletonPerName._id_to_name_map[id(instance)] = name
            info: SingletonInfo = SingletonInfo(name=name, val=instance)
            SingletonPerName._instances[k] = info
        else:
            info = SingletonPerName._instances[k]
        obj = info.val
        assert isinstance(obj, cls)
        return obj

    @staticmethod
    def delete_singleton_by_name(
        name: str, cls: Optional[Type[SingletonPerName]] = None
    ):
        """
        Delete the singleton instance with the given name.

        This can be used for testing to create another singleton.

        Args:
            name: The name of the singleton instance to delete.

            cls: The class of the singleton instance to delete. If not given, all
                instances with the given name are deleted.
        """
        for k, v in list(SingletonPerName._instances.items()):
            if k[1] == name:
                if cls is not None and v.cls != cls:
                    continue

                del SingletonPerName._instances[k]
                del SingletonPerName._id_to_name_map[id(v.val)]

    def delete_singleton(self):
        """
        Delete the singleton instance. Can be used for testing to create another
        singleton.
        """
        id_ = id(self)

        if id_ in SingletonPerName._id_to_name_map:
            name = SingletonPerName._id_to_name_map[id_]
            del SingletonPerName._id_to_name_map[id_]
            del SingletonPerName._instances[(self.__class__.__name__, name)]
        else:
            logger.warning("Instance %s not found in our records.", self)
