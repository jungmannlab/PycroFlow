from setuptools import setup, find_packages
 
classifiers = [
  'Development Status :: 5 - Production/Stable',
  'Intended Audience :: Education',
  'Operating System :: Microsoft :: Windows :: Windows 10',
  'License :: OSI Approved :: MIT License',
  'Programming Language :: Python :: 3'
]
 
setup(
  name='pyHamiltonPSD',
  version='1.1.0',
  description='Basic Hamilton CE PSD Application',
  long_description=open('README.txt').read() + '\n\n' + open('CHANGELOG.txt').read(),
  url='',  
  author='Hamilton CE - developed by Camil Milos',
  author_email='contact.hce.ro@hamilton-ce.com',
  license='MIT', 
  classifiers=classifiers,
  keywords='Hamilton PSD',
  packages=find_packages(),
  install_requires=['pyserial']
)