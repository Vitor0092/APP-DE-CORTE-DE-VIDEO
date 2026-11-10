"""Run heavy steps (download, transcription, video editing) in a separate process.

The Railway trial gives the whole site 1 GB of RAM. Running each heavy step in its own short-lived
process gives the memory back to the system when the step ends, and if memory still runs out the
kernel kills that helper process instead of the web server.
"""
import concurrent.futures as cf
import multiprocessing as mp
from concurrent.futures.process import BrokenProcessPool

OOM_MSG = ("O servidor ficou sem memória nesta etapa. Tente um vídeo mais curto, ou aumente o plano "
           "do Railway (Hobby) para ter mais memória.")


def _child_init():
    try:  # make this helper the first thing the kernel kills if memory runs out
        with open("/proc/self/oom_score_adj", "w") as f:
            f.write("1000")
    except OSError:
        pass


def _call(fn, args, kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as e:  # re-raise as a plain error so it always crosses the process boundary
        raise RuntimeError(str(e)) from None


def run(fn, *args, **kwargs):
    with cf.ProcessPoolExecutor(max_workers=1, mp_context=mp.get_context("spawn"),
                                initializer=_child_init) as ex:
        try:
            return ex.submit(_call, fn, args, kwargs).result()
        except BrokenProcessPool:
            raise RuntimeError(OOM_MSG) from None
