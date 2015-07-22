#!/usr/bin/env python
# *********************************************************************
# Y.A.N.C. - Yet Another Nixie Clock
#
# Author    : Steve Sirois
# Version   : A
# Date      : 2015-05-26
#
# Copyright 2015 Steve Sirois  (steve.sirois@gmail.com)
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
import time
import os
import sys
import stat
import socket
import sqlite3
import daemon
import signal
import logging 
import logging.handlers 
from lockfile.pidlockfile import PIDLockFile
import select

import multiprocessing
from multiprocessing import Process, Queue
from Queue import Empty

#import pygame  
#   Lack good support for MP3 playback so used Music Player Daemon

# **************************************************************************
# Contants section, know what you do :-)
#
# Setup IO (PP = Physical Pin, (l) = Denote an active state on low)
# Nixie Board
OUT_SRCLR = 26  # PP 37 - SCL [SRCLR] (l) pin on 74LS595 - 0 = clear, 1 = enable
OUT_LED = 12    # PP 32 - Ambient led driver - will be PWM driven
# Digital potentiometer
OUT_UP_DOWN = 22    # PP 15(7) - U/D(l) on DS1804
OUT_INC = 5         # PP 29(5) - INC(h) on DS1804
OUT_CS_MICRO = 13   # PP 31(3) - CS(l) on DS1804 for microphone sensitivity
# Audio enabled
OUT_AUDIO = 24      # PP 18(8) - AUDIO ENABLED on SSM2211
# Inputs
IN_NOISE_DETECT = 17    # PP 11(9) - Output of OpAmp TL084
IN_TOUCH_DETECT = 25    # PP 22(6) - Atmel AT42QT1011 QTouch Capacitive 
# Socket server for communication with uWSGI REST Server (yanc-REST)
SERVER_ADDRESS = '/tmp/uds_socket'
# PID & Log
PIDFILE = '/var/run/yanc-daemon.pid'
LOGFILE = '/var/log/yanc-daemon.log'
WORKING_DIR = '/home/pi/yanc'

# **************************************************************************
# Set logging level here
logger = logging.getLogger("yanc") 
logger.setLevel(logging.DEBUG) # DEBUG - INFO - WARNING - ERROR - CRITICAL

ch = logging.FileHandler(LOGFILE)
ch.setLevel(logging.DEBUG)

formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s") 
ch.setFormatter(formatter) 
logger.addHandler(ch)

# Define call back for external events (Mic + touch)
def my_callback(channel):
    pass

def my_callback2(channel):
    pass

def initIO():
    # Setup I/O
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)

    # OUTPUT
    GPIO.setup(OUT_SRCLR, GPIO.OUT, initial=GPIO.LOW) 
    GPIO.setup(OUT_LED, GPIO.OUT, initial=GPIO.LOW)
    GPIO.setup(OUT_UP_DOWN, GPIO.OUT, initial=GPIO.LOW) 
    GPIO.setup(OUT_INC, GPIO.OUT, initial=GPIO.LOW)
    # De-select microphone
    GPIO.setup(OUT_CS_MICRO, GPIO.OUT, initial=GPIO.HIGH)   
    # Turn off sound for now
    GPIO.setup(OUT_AUDIO, GPIO.OUT, initial=GPIO.HIGH)      

    # INPUT
    GPIO.setup(IN_NOISE_DETECT, GPIO.IN)
    GPIO.setup(IN_TOUCH_DETECT, GPIO.IN) 
    
    # Attach callback... not yet!
    #GPIO.add_event_detect(IN_NOISE_DETECT, GPIO.FALLING, callback=my_callback, bouncetime=300)  
    #GPIO.add_event_detect(IN_TOUCH_DETECT, GPIO.RISING, callback=my_callback2, bouncetime=300)  

def adjust_gain(incr, direction):
    usleep = lambda x: time.sleep(x / 1000000.0)
    GPIO.output(OUT_CS_MICRO, GPIO.LOW)

    if direction == 'UP':
        GPIO.output(OUT_UP_DOWN, GPIO.HIGH)    
    else:
        GPIO.output(OUT_UP_DOWN, GPIO.LOW)    

    for x in range(incr):
        GPIO.output(OUT_INC, GPIO.HIGH) 
        usleep(1000)
        GPIO.output(OUT_INC, GPIO.LOW)    
        usleep(1000)

    GPIO.output(OUT_CS_MICRO, GPIO.HIGH)

def show_led(q_led):

    led = GPIO.PWM(OUT_LED, 70) # 70 Hz refresh
    data = None

    while True:
        data = q_led.get() # Blocking is ok here...
        logger.debug('q_led received : {0}'.format(data))
        if data >= 0.0:
            led.start(data)
        elif data == -1:
            break

    led.start(0)
    logger.info('show_led is done.')

def play_music(q_music):
    #pygame.mixer.init()

    data = None

    while True:
        data = q_music.get() # Blocking is ok
        logger.debug('q_music received : {0}'.format(data))
        
        if data == 'PLAY':
            #pygame.mixer.music.load("music/02WhatMoreCanISay.ogg")
            #pygame.mixer.music.set_volume(0.5)
            #pygame.mixer.music.play()
            GPIO.output(OUT_AUDIO, GPIO.LOW)
        elif data == 'PAUSE':
            #pygame.mixer.music.pause()
            GPIO.output(OUT_AUDIO, GPIO.HIGH)
        elif data == 'UNPAUSE':
            #pygame.mixer.music.unpause()
            GPIO.output(OUT_AUDIO, GPIO.LOW)
        elif data == 'STOP':
            #pygame.mixer.music.stop()
            GPIO.output(OUT_AUDIO, GPIO.HIGH)
        elif data == 'QUIT':
            break

    #pygame.mixer.quit()
    GPIO.output(OUT_AUDIO, GPIO.HIGH)
    logger.info('play_music is done.')

def show_nixie(q_display):
    # Use SPI to 'talk' to the shift register (74LS595)
    # No bit packing here! :-)
    spi = spidev.SpiDev()
    spi.open(0,0) # port 0, device CE0 [BCM 8 / PP 24]
    # Other detail : SCLK [BCM 11 / PP 23], MOSI [BCM 10 / PP 19]    
    spi.xfer2([0x00])
    spi.xfer2([0x00])
    GPIO.output(OUT_SRCLR, GPIO.HIGH)

    usleep = lambda x: time.sleep(x / 1000000.0)

    rate = 70 # (Hz) must be > 60 to get to "flicker fusion threshold" :-)
    cycle = 1000000.0 / rate / 4 # (us) one full cycle per nixie (4)
    blanking = 300 # (us) - prevent ghosting effect
    turnon = 100 # (us) Turn-on time, typical between 10-100

    data = None

    while True:
        try:
            data = q_display.get_nowait()
            # Must not block! :-) check link below for more info
            # stackoverflow.com/feeds/question/31235112
        except Empty:  # queue was empty, better chance next time
            pass

        if data == None:
            pass
        elif data == 'QUIT':
            break
        elif data == 'OFF':
            GPIO.output(OUT_SRCLR, GPIO.LOW)
        elif data == 'ON':
            GPIO.output(OUT_SRCLR, GPIO.HIGH)
        else:
            tube = 8
            for d in data:
                if tube == 8 and d == "0":  # don't turn on first digit
                    off_hex = "00"
                    on_hex = "00"
                else:
                    off_hex = "0" + d 
                    on_hex = str(tube) + d 

                spi.xfer2([int(off_hex, 16)])
                usleep(blanking) # with d between 0-9, one cathode is always on
                spi.xfer2([int(on_hex, 16)])
                usleep(cycle - blanking + turnon)

                # Next tube
                tube = tube >> 1

    # Close all
    spi.xfer2([0x00])
    spi.xfer2([0x00])
    spi.close()
    GPIO.output(OUT_SRCLR, GPIO.LOW)
    logger.info('show_nixie is done.')

class SockServer(object):
    
    def __init__(self, port, queue):
        logger.debug('init SockServer')
        # Start with a clean slate...
        try:
            os.unlink(port)
        except OSError:
            if os.path.exists(port):
                logger.critical('Problem binding UDS Socket {0}'.format(port))
                raise

        # Open and bind server
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(port)
        self.server.listen(1)
        
        # Make sure 'other' can talk to socket
        mode = os.stat(port)
        os.chmod(port, mode.st_mode | stat.S_IWOTH)
        
        self.queue = queue
        self.outputs = []

    def listen(self):
        logger.debug('sock listen')
        qdata = None
        inputs = [self.server]

        while qdata != 'QUIT':
            try:
                qdata = self.queue.get_nowait() # none blocking, IMPORTANT!
            except Empty:  # queue was empty, better chance next time
                pass

            try:
                # Wait for activity, but only for half second...
                r,w,e = select.select(inputs, self.outputs, [], 0.5)
            except select.error, e:
                logger.error('Problem on select.select in sock server!?')
                break

            for s in r:
                if s == self.server:
                    client, address = self.server.accept()
                    logger.debug('connection from {0}'.format(address))
                    data = client.recv(16)
                    logger.debug('...received {0}'.format(data))
                    client.send(data)
                    # Add the msg router later...
                    q_music.put(data) # temp!
                    # This server is not very chatty!
                    client.close()

        self.server.close()
        logger.info('sock is done.')

def lauch_server(q_sock):
    SockServer(port=SERVER_ADDRESS,queue=q_sock).listen()

def ignore(signal, frame):
    pass

def cleanup(signal, frame):
    logger.info('main stopped!')
    q_display.put('QUIT')
    q_music.put('QUIT')
    q_sock.put('QUIT')
    q_led.put(-1)
    sys.exit(0)
    # DON'T CLEANUP GPIO!
    #   It leave the I/O in a high impedance state that put the
    #   OUT_AUDIO pin in an unhappy state that in turn put static in the speaker
    #GPIO.cleanup()

def main():
    # **************************************************************************
    # Main Loop

    # need global so any process can talk to it
    global q_display, q_led, q_music, q_sock
    q_display = Queue()
    q_led = Queue()
    q_music = Queue()
    q_sock = Queue()

    initIO()

    nixie = Process(target=show_nixie, args=(q_display,)).start()
    led = Process(target=show_led, args=(q_led,)).start()
    music = Process(target=play_music, args=(q_music,)).start()
    sock = Process(target=lauch_server, args=(q_sock,)).start()

    logger.info('main started!')
 
    while True:
        q_display.put(time.strftime('%H%M'))
        time.sleep(15)

# Invoke this daemon!
context = daemon.DaemonContext(
    working_directory=WORKING_DIR,
    umask=0o002,
    pidfile=PIDLockFile(PIDFILE, timeout=2),
    stdout=sys.stdout, # ok for dev phase
    stderr=sys.stderr, # ok for dev phase
    files_preserve=[ch.stream,]
    )

context.signal_map = {
    signal.SIGTERM: cleanup,
    signal.SIGHUP: 'terminate',
    signal.SIG_IGN: ignore 
    # This last one must be there otherwise error on context open:
    #     TypeError: signal handler must be signal.SIG_IGN, ....
    }

with context:
    main()
