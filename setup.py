import setuptools
from typing import List


def read_multiline_as_list(file_path: str) -> List[str]:
    with open(file_path) as fh:
        contents = fh.read().split("\n")
        if contents[-1] == "":
            contents.pop()
        return contents


with open("README.md", "r") as fh:
    long_description = fh.read()

requirements = read_multiline_as_list("requirements.txt")

classifiers = read_multiline_as_list("classifiers.txt")

setuptools.setup(
    name="git-annex-remote-manyzips",
    version="0.0.1",
    author="Nei Cardoso de Oliveira Neto",
    author_email="nei.neto@hotmail.com",
    description="",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/cardoso-neto/git-annex-remote-manyzips",
    packages=setuptools.find_packages(),
    classifiers=classifiers,
    keywords='git annex external remote manyzips zip',
    entry_points = {
        'console_scripts': [
            'git-annex-remote-manyzips=git_annex_remote_manyzips.manyzips:main',
        ],
    },
    python_requires=">=3.8",
    install_requires=requirements,
)
