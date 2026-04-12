# setup.py
from setuptools import setup, find_packages

setup(
    name='kritomatic',
    version='1.0.0',
    py_modules=['main', 'client', 'compiler', 'decorators'],
    entry_points={
        'console_scripts': [
            'kritomatic=main:main',
        ],
    },
    install_requires=['pyyaml'],
)
