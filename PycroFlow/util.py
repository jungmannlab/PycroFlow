"""
uil.py

Provides a uility functions.
"""
import time


def fmt_time_delta(time_delta, width=20):
	"""Formats the time delta in a readable way
	Args:
		time_delta : float
			the time in s
	Returns:
		fmt_dt : str
			the formatted time delta
	"""
	days = int(time_delta // (3600 * 24))
	hrs = int(time_delta // 3600)
	mins = int(time_delta // 60)
	secs = int(time_delta % 60)
	msecs = int(1000 * (time_delta % 1))
	numbers = [days, hrs, mins, secs, msecs]
	units = ['days', 'hours', 'min', 's', 'ms']
	snippets = [
		'{:d} {:s}'.format(n, u)
		for n, u in zip(numbers, units)
		if n > 0]
	fmt_dt = ' '.join(snippets)
	if len(fmt_dt) > width:
		fmt_dt = fmt_dt[:width]
	return fmt_dt + ' ' * (width - len(fmt_dt))


class ProgressBar:
	def __init__(self, title, n_frames, charwidth=40, timewidth=20):
	    nimgs_total = n_frames
	    #sys.stdout.write(title + ": [" + "-"*40 + "]" + chr(8)*41)
	    #sys.stdout.flush()
	    print(title + ": [" + "-"*charwidth + "]", end='\r')
	    self.progress_x = 0
	    self.title = title
	    self.nimgs_acquired = 0
	    self.nimgs_total = n_frames
	    self.charwidth = charwidth
	    self.start_time = time.time()
	    self.timewidth = timewidth

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
	    if x == 1:
	    	chardeci = ''
	    charrest = self.charwidth - charfull - 1
	    deltat = time.time() - self.start_time
	    time_left = (1 - x) / x * deltat
	    print(
	    	self.title
	    	+ ": [" + '#'*charfull + str(chardeci) +"-"*charrest + "]"
	    	+ "  {:d}/{:d}".format(1+int(x*self.nimgs_total), self.nimgs_total)
	    	+ " time remaining: {:s}".format(fmt_time_delta(time_left, self.timewidth)),
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
	    	+ " " * (4 +2 * len(str(self.nimgs_total)))
	    	+ " " * self.timewidth,
	    	end='\n')
