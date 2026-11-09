# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
from setuptools import find_packages, setup

setup(
    name="deck-deck-goose",
    version="0.6.3-wip",
    description="A logical This PC folder (disks, Users, Documents) for Linux file managers, plus a Steam game manager (move, clean, compress)",
    author="Matthew Armstrong",
    author_email="The-Dorkknight@users.noreply.github.com",
    url="https://github.com/The-Dorkknight/deck-deck-goose",
    license="PolyForm-Noncommercial-1.0.0",
    package_dir={"": "src"},
    packages=find_packages("src"),
    python_requires=">=3.8",
    entry_points={"console_scripts": ["ddg=ddg.__main__:main"]},
)
