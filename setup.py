from setuptools import setup, find_packages
 
classifiers = [
  'Development Status :: Development',
  'Intended Audience :: Education',
  'Operating System :: Microsoft :: Windows :: Windows 10',
  'License :: OSI Approved :: MIT License',
  'Programming Language :: Python :: 3'
]
 
setup(
  name='PycroFlow',
  version='0.0.1',
  description='Microscopy and Fluid automation coordination',
  long_description=open('README.md').read() + '\n\n' + open('CHANGELOG.txt').read(),
  url='',  
  # author='MPG - Heinrich Grabmayr',
  # author_email='hgrabmayr@biochem.mpg.de',
  license='MIT', 
  classifiers=classifiers,
  keywords='pycromanager',
  packages=find_packages(),
  install_requires=['pyserial']
)