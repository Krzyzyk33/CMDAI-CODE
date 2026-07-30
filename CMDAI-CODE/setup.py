import os
from setuptools import setup, find_packages

def read_requirements():
    req_file = os.path.join(os.path.dirname(__file__), "requirements.txt")
    if os.path.exists(req_file):
        with open(req_file, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip() and not line.startswith("#")]
    return []

setup(
    name="cmdai-code",
    version="2.6.0",
    packages=find_packages(),
    install_requires=read_requirements(),
    entry_points={
        'console_scripts': [
            'cmdai-code=src.main:main',
            'cmdai_code=src.main:main',
            'cmdaicode=src.main:main',
        ],
    },
)
