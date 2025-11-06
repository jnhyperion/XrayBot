import os
import sys
import shutil
from invoke import task
from glob import glob


def _get_ctx_abs_path(ctx, *path) -> str:
    return os.path.join(os.path.abspath(ctx.cwd), *path)


@task
def init(ctx):
    ctx.run("pre-commit install")


@task
def clean(_):
    shutil.rmtree("dist", ignore_errors=True)
    shutil.rmtree("build", ignore_errors=True)
    shutil.rmtree(".pytest_cache", ignore_errors=True)
    shutil.rmtree(os.path.join("tests", ".pytest_cache"), ignore_errors=True)
    shutil.rmtree(os.path.join("tests", "__coverage__"), ignore_errors=True)
    egg_info_files = glob(os.path.join("src", "*.egg-info"))
    for f in egg_info_files:
        shutil.rmtree(f, ignore_errors=True)


@task(clean)
def build(ctx):
    ctx.run("pip install --upgrade build")
    ctx.run(f"{sys.executable} -m build")
    assert glob(os.path.join("dist", "*.whl"))
    assert glob(os.path.join("dist", "*.tar.gz"))


@task
def pre_commit(ctx):
    ctx.run("pre-commit run --all-files")


@task(build)
def publish(ctx):
    ctx.run("pip install --upgrade twine")
    cmd = f"{sys.executable} -m twine upload dist/*"
    ctx.run(cmd)
