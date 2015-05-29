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

import spidev
import RPi.GPIO as GPIO
from multiprocessing import Process, Queue

# Setup IO
GPIO.setmode(GPIO.BCM)
GPIO.setup(26, GPIO.OUT) # PP 37 - SCL (SRCLR) pin on 74LS595 - 0 = clear, 1 = enable
GPIO.setup(12, GPIO.OUT) # PP 32 - Ambient led driver - will be PWM driven

# 
p = GPIO.PWM(12, 100) # ,100 = 100 Hz
p.start(50) # DutyCycle 50%
p.ChangeDutyCycle(90)
p.stop() 

#GPIO.cleanup()

def ShowOnNixie(q):
	# I use SPI to 'talk' to my shift register (74LS595)
	# No bit packing here! :-)

	spi = spidev.SpiDev()
	spi.open(0,0) # port 0, device CE0 [BCM 8 / PP 24]
	# Other detail : SCLK [BCM 11 / PP 23], MOSI [BCM 10 / PP 19]

	while True:
		# Get next display
		item = q.get()
		print "hello!"
		spi.xfer2([0x20]) // 5 allume
		spi.xfer2([0x11]) // 4 allume

	
	spi.close()

# Main Loop
if __name__ == ‘__main__’:
	# Setup process for display and playback
	q = Queue()
	p = Process(target=ShowOnNixie, args=(q,))
	p.start()


	#p.join()


