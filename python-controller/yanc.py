#!/usr/bin/env python
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
import web
import time
from multiprocessing import Process, Queue

# Setup IO (PP = Physical Pin)
# Nixie Board
out_SRCLR = 26 # PP 37 - SCL (SRCLR) pin on 74LS595 - 0 = clear, 1 = enable
out_led = 12 # PP 32 - Ambient led driver - will be PWM driven

# Digital potentiometer
out_up-down = 5 # PP 29 - U/D on DS1804
out_inc = 6 # PP 31 - INC on DS1804
out_cs_audio = 13 # PP 33 - CS on DS1804 for audio level playback
out_cs_micro = 17 # PP 11 - CS on DS1804 for microphone sensitivity

in_noise_detect = 23  # PP 16 - Ouput of OpAmp
in_touch_detect = 24 # PP 18 - Atmel AT42QT1011

GPIO.setmode(GPIO.BCM)

GPIO.setup(out_SRCLR, GPIO.OUT) 
GPIO.setup(out_led, GPIO.OUT)

GPIO.setup(out_up-down, GPIO.OUT) 
GPIO.setup(out_inc, GPIO.OUT) 
GPIO.setup(out_cs_audio, GPIO.OUT) 
GPIO.setup(out_cs_micro, GPIO.OUT)

GPIO.setup(in_noise_detect, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(in_touch_detect, GPIO.IN, pull_up_down=GPIO.PUD_UP) 

# Spare pins
#GPIO.setup(22, GPIO.OUT) # PP 15
#GPIO.setup(25, GPIO.IN) # PP 22

def my_callback(channel):
	print "falling edge detected on 23"

def my_callback2(channel):
	print "falling edge detected on 24"

GPIO.add_event_detect(23, GPIO.FALLING, callback=my_callback, bouncetime=300)  
GPIO.add_event_detect(24, GPIO.FALLING, callback=my_callback2, bouncetime=300)  

#GPIO.wait_for_edge(24, GPIO.RISING)

# 
#p = GPIO.PWM(12, 100) # ,100 = 100 Hz
#p.start(50) # DutyCycle 50%
#p.ChangeDutyCycle(90)
#p.stop() 

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
	# Setup process for display, playback and interrupt input
	q = Queue()
	p = Process(target=ShowOnNixie, args=(q,))
	p.start()


	#p.join()
