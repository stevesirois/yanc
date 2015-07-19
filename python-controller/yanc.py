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
import signal
import sqlite3
import pygame

from multiprocessing import Process, Queue
from Queue import Empty

# Setup IO (PP = Physical Pin, (l) = Denote an active state on low)
# Nixie Board
out_SRCLR = 26  # PP 37 - SCL [SRCLR] (l) pin on 74LS595 - 0 = clear, 1 = enable
out_led = 12    # PP 32 - Ambient led driver - will be PWM driven

# Digital potentiometer
out_up_down = 22    # PP 15(7) - U/D(l) on DS1804
out_inc = 5         # PP 29(5) - INC(h) on DS1804
out_cs_micro = 13    # PP 31(3) - CS(l) on DS1804 for microphone sensitivity
# Audio enabled
out_audio = 24      # PP 18(8) - AUDIO ENABLED on SSM2211
# Inputs
in_noise_detect = 17    # PP 11(9) - Output of OpAmp TL084
in_touch_detect = 25    # PP 22(6) - Atmel AT42QT1011 QTouch Capacitive 

# Socket server for communication with uWSGI REST Server (yanc-REST)
server_address = '/tmp/uds_socket'

# Setup I/O
GPIO.setwarnings(False)
GPIO.setmode(GPIO.BCM)

GPIO.setup(out_SRCLR, GPIO.OUT, initial=GPIO.LOW) 
GPIO.setup(out_led, GPIO.OUT, initial=GPIO.LOW)

GPIO.setup(out_up_down, GPIO.OUT, initial=GPIO.LOW) 
GPIO.setup(out_inc, GPIO.OUT, initial=GPIO.LOW) 
GPIO.setup(out_audio, GPIO.OUT, initial=GPIO.HIGH)      # Turn off sound for now
GPIO.setup(out_cs_micro, GPIO.OUT, initial=GPIO.HIGH)   # De-select microphone

GPIO.setup(in_noise_detect, GPIO.IN)
GPIO.setup(in_touch_detect, GPIO.IN) 

# Define call back for external events (Mic + touch)
def my_callback(channel):
    print 'microphone detect'

def my_callback2(channel):
    print 'touch detect'

#GPIO.add_event_detect(in_noise_detect, GPIO.FALLING, callback=my_callback, bouncetime=300)  
#GPIO.add_event_detect(in_touch_detect, GPIO.RISING, callback=my_callback2)  

def adjust_gain(incr, direction):
    usleep = lambda x: time.sleep(x / 1000000.0)
    GPIO.output(out_cs_micro, GPIO.LOW)

    if direction == 'UP':
        GPIO.output(out_up_down, GPIO.HIGH)    
    else:
        GPIO.output(out_up_down, GPIO.LOW)    

    for x in range(incr):
        GPIO.output(out_inc, GPIO.HIGH) 
        usleep(1000)
        GPIO.output(out_inc, GPIO.LOW)    
        usleep(1000)

    GPIO.output(out_cs_micro, GPIO.HIGH)

def show_led(q_led):
    # Make sure child process ignore ctrl-c for clean stop
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    led = GPIO.PWM(out_led, 70) # 70 Hz refresh
    data = None

    while True:
        data = q_led.get()
        
        if data >= 0.0:
            led.start(data)
        elif data == -1:
            break

    led.start(0)
    print 'show_led is done.'

def play_music(q_music):
    # Make sure child process ignore ctrl-c for clean stop
    signal.signal(signal.SIGINT, signal.SIG_IGN)
     
    #pygame.init()
    #pygame.mixer.init()

    data = None

    while True:
        data = q_music.get()

        if data == 'PLAY':
            #pygame.mixer.music.load("music/02WhatMoreCanISay.ogg")
            #pygame.mixer.music.set_volume(0.5)
            #pygame.mixer.music.play()
            GPIO.output(out_audio, GPIO.LOW)
        elif data == 'PAUSE':
            #pygame.mixer.music.pause()
            GPIO.output(out_audio, GPIO.HIGH)
        elif data == 'UNPAUSE':
            #pygame.mixer.music.unpause()
            GPIO.output(out_audio, GPIO.LOW)
        elif data == 'STOP':
            #pygame.mixer.music.stop()
            GPIO.output(out_audio, GPIO.HIGH)
        elif data == 'QUIT':
            break

    #pygame.mixer.quit()
    GPIO.output(out_audio, GPIO.HIGH)
    print 'play_music is done.'


def show_nixie(q_display):
    # Make sure child process ignore ctrl-c for clean stop
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    # Use SPI to 'talk' to the shift register (74LS595)
    # No bit packing here! :-)
    spi = spidev.SpiDev()
    spi.open(0,0) # port 0, device CE0 [BCM 8 / PP 24]
    # Other detail : SCLK [BCM 11 / PP 23], MOSI [BCM 10 / PP 19]    
    spi.xfer2([0x00])
    spi.xfer2([0x00])
    GPIO.output(out_SRCLR, GPIO.HIGH)

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
            GPIO.output(out_SRCLR, GPIO.LOW)
        elif data == 'ON':
            GPIO.output(out_SRCLR, GPIO.HIGH)
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
    GPIO.output(out_SRCLR, GPIO.LOW)
    print 'show_nixie is done.'

def gracefull_quit(signal, frame):
        print '\nKill - main stopped!'
        q_display.put('QUIT')
        q_led.put(-1)
        q_music.put('QUIT')
        # DON'T CLEANUP!
        #   It leave the I/O in a high impedance state that put the
        #   out_audio pin in an unhappy state that put static in the speaker
        #GPIO.cleanup()
        sys.exit(0)

def sock_server(q_sock):
    # Make sure the socket does not already exist
    try:
        os.unlink(server_address)
    except OSError:
        if os.path.exists(server_address):
            raise

    # Create the UDS socket
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(server_address)
    sock.listen(1)

    # Make sure 'other' can talk to socket
    mode = os.stat(server_address)
    os.chmod(server_address, mode.st_mode | stat.S_IWOTH)

    while True:
        # Wait for a connection
        print >>sys.stderr, 'waiting for a connection'
        connection, client_address = sock.accept()
        try:
            print >>sys.stderr, 'connection from', client_address

            # Receive the data in small chunks and retransmit it
            while True:
                data = connection.recv(16)
                print >>sys.stderr, 'received "%s"' % data
                if data:
                    print >>sys.stderr, 'sending OK to client'
                    connection.send(data)
                    # Data is two part : Queue Name and data
                    q_music.put(data)
                else:
                    print >>sys.stderr, 'no more data from', client_address
                    break
                
        finally:
            # Clean up the connection
            connection.close()

# ******************************************************************************
# Main Loop
signal.signal(signal.SIGTERM, gracefull_quit)

if __name__ == '__main__':
    # need global so any process can talk to it
    global q_display, q_led, q_music, q_sock  
    q_display = Queue()
    q_led = Queue()
    q_sock = Queue() 
    q_music = Queue()

    nixie = Process(target=show_nixie, args=(q_display,)).start()
    led = Process(target=show_led, args=(q_led,)).start()
    music = Process(target=play_music, args=(q_music,)).start()
    sock = Process(target=sock_server, args=(q_sock,)).start()

    print 'main started!'
    try:
        while True:
            q_display.put(time.strftime('%H%M'))
            time.sleep(15)

    except KeyboardInterrupt:
        print '\nCtrl-C - main stopped!'
        q_display.put('QUIT')
        q_led.put(-1)
        q_music.put('QUIT')
        # DON'T CLEANUP!
        #   It leave the I/O in a high impedance state that put the
        #   out_audio pin in an unhappy state that put static in the speaker
        #GPIO.cleanup()
