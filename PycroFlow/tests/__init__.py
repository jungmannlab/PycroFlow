"""
usage:

$ cd /Users/hgrabmayr/GitHub/PycroFlow
$ python -m unittest -v
"""
import shutil
import os


try:
	shutil.rmtree('PycroFlow//TestData')
except:
	pass
os.mkdir('PycroFlow//TestData')
