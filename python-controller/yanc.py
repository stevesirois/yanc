# *********************************************************************
# Y.A.N.C. - Yet Another Nixie Clock
#
# Author	: Steve Sirois
# Version	: A
# Date		: 2015-05-26
#
# Copyright © 2015 Steve Sirois  (steve.sirois@gmail.com)
# *********************************************************************
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>
#

from multiprocessing import Process, Queue

def ShowOnNixie(q):
	while True:
		# Get next display
		item = q.get()
		print "hello!"


# Main Loop
if __name__ == ‘__main__’:
	# Setup process for display and playback
	q = Queue()
	p = Process(target=ShowOnNixie, args=(q,))
	p.start()


	#p.join()


