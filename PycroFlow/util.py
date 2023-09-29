"""
uil.py

Provides a uility functions.
"""
class ProgressBar:
	def __init__(self, title, n_frames, charwidth=40):
	    nimgs_total = n_frames
	    #sys.stdout.write(title + ": [" + "-"*40 + "]" + chr(8)*41)
	    #sys.stdout.flush()
	    print(title + ": [" + "-"*charwidth + "]", end='\r')
	    self.progress_x = 0
	    self.title = title
	    self.nimgs_acquired = 0
	    self.nimgs_total = n_frames
	    self.charwidth = charwidth

	def progress(self, x):
	    """Updates the progress bar
	    Args:
	        x : float
	            progress in fraction (0-1)
	    """
	    charprog = x * self.charwidth
	    charfull = int(charprog)
	    chardeci = int((charprog-charfull) * 10)
	    if chardeci > 9:
	        chardeci = 0
	    charrest = self.charwidth - charfull - 1
	    print(
	    	self.title
	    	+ ": [" + '#'*charfull + str(chardeci) +"-"*charrest + "]"
	    	+ "  {:d}/{:d}".format(1+int(x*self.nimgs_total), self.nimgs_total),
	    	end='\r')
	    #print(x, y, deci, x+y+1)

	def progress_increment(self):
		"""increments nimgs_acquired by 1 and calls progress()
		"""
		self.nimgs_acquired += 1
		self.progress(self.nimgs_acquired/self.nimgs_total)

	def end_progress(self):
	    #sys.stdout.write("#" * (40 - progress_x) + "]\n")
	    #sys.stdout.flush()
	    print(
	    	self.title
	    	+ ": [" + "#"*self.charwidth + "]"
	    	+ " " * (4 +2 * len(str(self.nimgs_total))),
	    	end='\n')
