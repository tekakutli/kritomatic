from setuptools import setup, find_packages

setup(
    name='kritomatic',
    version='1.0.0',
    description='Command-line interface for Krita automation',
    long_description=open('README.md').read(),
    long_description_content_type='text/markdown',
    author='tekakutli',
    license='AGPLv3',
    package_dir={'': 'src'},
    packages=find_packages(where='src'),
    include_package_data=True,
    entry_points={
        'console_scripts': [
            'kritomatic=kritomatic.__main__:main',
        ],
    },
    install_requires=[
        'pyyaml',
        'PyQt5>=5.15.11',
    ],
)
