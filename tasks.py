import os
import sys
import shutil
from pathlib import Path
from invoke import task


def _get_ctx_abs_path(ctx, *path) -> str:
    return os.path.join(os.path.abspath(ctx.cwd), *path)


@task
def init(ctx):
    ctx.run("pre-commit install")


@task
def clean(ctx):
    shutil.rmtree(
        _get_ctx_abs_path(ctx, "htmlcov"),
        ignore_errors=True,
    )
    shutil.rmtree(
        _get_ctx_abs_path(ctx, ".pytest_cache"),
        ignore_errors=True,
    )
    shutil.rmtree(
        _get_ctx_abs_path(ctx, ".tox"),
        ignore_errors=True,
    )
    Path(_get_ctx_abs_path(ctx, ".coverage")).unlink(missing_ok=True)
    shutil.rmtree(_get_ctx_abs_path(ctx, "build"), ignore_errors=True)
    shutil.rmtree(_get_ctx_abs_path(ctx, "dist"), ignore_errors=True)
    shutil.rmtree(
        _get_ctx_abs_path(ctx, "xray_bot.egg-info"),
        ignore_errors=True,
    )


@task(clean)
def build(ctx):
    ctx.run(f"{sys.executable} setup.py bdist_wheel", hide="out")


@task
def pre_commit(ctx):
    ctx.run("pre-commit run --all-files")


@task(build)
def publish(ctx):
    ctx.run(f"{sys.executable} -m twine upload dist/*")
