"""
usage:

$ cd /Users/hgrabmayr/GitHub/PycroFlow
$ python -m unittest -v
"""
import shutil
import os


shutil.rmtree('PycroFlow//TestData')
os.mkdir('PycroFlow//TestData')
