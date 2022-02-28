import os
from setuptools import setup, find_packages

REQUIREMENTS = [i.strip() for i in open("requirements.txt").readlines()]

about = {}
with open(os.path.join("xraybot", "__version__.py")) as f:
    exec(f.read(), about)

VERSION = about["__version__"]

setup(
    version=VERSION,
    name="xray-bot",
    packages=find_packages(),
    description=f"Synchronize atlassian xray test case tickets with your test code and upload the test results",
    author="Johnny Huang",
    author_email="jnhyperion@gmail.com",
    url="https://github.com/jnhyperion/XrayBot",
    keywords="atlassian xray automation",
    classifiers=[
        "Programming Language :: Python :: 3",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.8",
    install_requires=REQUIREMENTS,
    include_package_data=True,
)
