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
import requests
import json
import sqlite3 as sqlite
import dateutil.parser
from dateutil.tz import tzlocal
import datetime
from enum import Enum
import multiprocessing
from multiprocessing import Process, Queue
from Queue import Empty
from subprocess import call

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
# Timing
REFRESH_RATE = 70       # Hz

CYCLE_MAIN_WAIT = 15    # Sec.
CYCLE_MUSIC_WAIT = 0.5  # Sec.

REFRESH_ALARMS = 3600   # Sec.
CHECK_ALARM = 60        # Sec.
SNOOZE_DELAY = 300      # Sec.

# **************************************************************************
# Set logging level here
logger = logging.getLogger("yanc") 
logger.setLevel(logging.DEBUG) # DEBUG - INFO - WARNING - ERROR - CRITICAL

fh = logging.FileHandler(LOGFILE)
fh.setLevel(logging.DEBUG)

formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s") 
fh.setFormatter(formatter) 
logger.addHandler(fh)

# **************************************************************************
# Load private info into JSON Object - SHOULD BE IN SAME DIR AS THIS FILE
with open(os.path.join(os.path.dirname(os.path.realpath(__file__)),
        'private_info.json')) as data_file:
    myprivateinfo = json.load(data_file)

# **************************************************************************
# Enum
Message = Enum('Message', 'alarm quit play unpause stop touch sound hold')
State = Enum('State', 'sleeping alarm snooze')

def callback_noise():
    #logger.debug('somebody yell at me!!!')
    pass

def callback_touch():
    logger.debug('somebody touch me!!!')
    q_music.put(Message.touch)
    pass

def sensor_detect(q_sensor):
    qdata = None
    state_t = GPIO.input(IN_TOUCH_DETECT)
    state_n = GPIO.input(IN_NOISE_DETECT)

    while True:
        try:
            qdata = q_sensor.get_nowait()
        except Empty:  # queue was empty, better chance next time
            pass

        if qdata == Message.quit:
            break
        
        state_now = GPIO.input(IN_TOUCH_DETECT)

        if state_now and state_t:
            pass    #no change
        elif state_now == True and state_t == False:
            # detect only rising transition
            state_t = state_now
            callback_touch()
        else:
            state_t = state_now

        state_now = GPIO.input(IN_NOISE_DETECT)

        if state_now and state_n:
            pass    #no change
        elif state_now == True and state_n == False:
            # detect only rising transition
            state_n = state_now
            callback_noise()
        else:
            state_n = state_now

        # Ok, don't get to crazy about reading those sensors!!!
        time.sleep(0.05)

    logger.info('sensors is done.')

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

    # INPUT (no pullup or pulldown resistor required here)
    GPIO.setup(IN_NOISE_DETECT, GPIO.IN)
    GPIO.setup(IN_TOUCH_DETECT, GPIO.IN) 
    
    # Attach callback... NOT!!!
    #   As version 0.5.11 of RPi.GPIO lib, those threaded callback work fine in
    #   a single process app. But when I run this in my multiprocess app,
    #   GPIO detect multiple rising edge event instead of one?!?
    #   So, had to implement my own loop to check pin input status.
    #GPIO.add_event_detect(IN_NOISE_DETECT, GPIO.FALLING, callback=callbackX)  
    #GPIO.add_event_detect(IN_TOUCH_DETECT, GPIO.RISING, callback=callbackY)  

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
    qdata = None

    while True:
        try:
            qdata = q_led.get_nowait()
        except Empty:  # queue was empty, better chance next time
            pass
        else:
            logger.debug('q_led received : {0}'.format(qdata))

        if qdata == Message.quit:
            break

        if qdata >= 0.0:
            led.start(qdata)

        qdata = None

    led.start(0)
    logger.info('show_led is done.')

def play_music(q_music):
    state = State.sleeping
    volume = 0
    flag_snooze = SNOOZE_DELAY / CYCLE_MUSIC_WAIT

    qdata = None

    while True:
        try:
            qdata = q_music.get_nowait()
        except Empty:  # queue was empty, better chance next time
            pass
        else:
            logger.debug('q_music received : {0}'.format(qdata))

        if qdata == Message.quit:
            break
        
        if qdata == Message.alarm:
            # Change states
            state = State.alarm
            call (['mpc','volume','{0}'.format(volume)])
            call (['mpc','play'])
            GPIO.output(OUT_AUDIO, GPIO.LOW)

        if qdata == Message.touch:
            # If we are in alarm, it mean snooze...
            if state == State.alarm:
                state = State.snooze
                call (['mpc','pause'])
                GPIO.output(OUT_AUDIO, GPIO.HIGH)
                volume = 0
                flag_snooze = SNOOZE_DELAY / CYCLE_MUSIC_WAIT

        if qdata == Message.unpause:
            state = State.alarm
            call (['mpc','volume','{0}'.format(volume)])
            call (['mpc','toggle'])
            GPIO.output(OUT_AUDIO, GPIO.LOW)    
            flag_snooze = SNOOZE_DELAY / CYCLE_MUSIC_WAIT

        qdata = None

        if state == State.alarm:
            volume += 2
            # Check maximum volume later
            if volume < 100:
                call (['mpc','volume','{0}'.format(volume)])

        if state == State.snooze:
            flag_snooze = timed_func(flag_snooze,SNOOZE_DELAY,
            CYCLE_MUSIC_WAIT,lambda : q_music.put(Message.unpause))

        # Take a brake...
        time.sleep(CYCLE_MUSIC_WAIT)

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

    rate = REFRESH_RATE # (Hz) must be > 60 to get to "flicker fusion threshold"
    cycle = 1000000.0 / rate / 4 # (us) one full cycle per nixie (4)
    blanking = 300 # (us) - prevent ghosting effect
    turnon = 100 # (us) Turn-on time, typical between 10-100 us

    qdata = None

    while True:
        try:
            qdata = q_display.get_nowait()
            # Must not block! :-) check link below for more info
            # stackoverflow.com/feeds/question/31235112
        except Empty:  # queue was empty, better chance next time
            pass

        if qdata == None:
            pass
        elif qdata == Message.quit:
            break
        # elif qdata == 'OFF':
        #     GPIO.output(OUT_SRCLR, GPIO.LOW)
        # elif qdata == 'ON':
        #     GPIO.output(OUT_SRCLR, GPIO.HIGH)
        else:
            tube = 8
            for d in qdata:
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

        while qdata != Message.quit:
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
    q_display.put(Message.quit)
    q_music.put(Message.quit)
    q_sock.put(Message.quit)
    q_sensor.put(Message.quit)
    q_led.put(Message.quit)
    sys.exit(0)
    # DON'T CLEANUP GPIO!
    #   It leave the I/O in a high impedance state that put the
    #   OUT_AUDIO pin in an unhappy state that in turn put static in the speaker
    #GPIO.cleanup()

def refresh_alarms():
    # Don't reinvent the wheel, call the REST server 
    logger.info('refresh thoses alarms...')
    
    try:
        url = 'http://raspberrypi:5000/yanc/api/v1.0/next-alarm'
        r = requests.get(url)
    except Exception, e:
        logger.error('Problem with request %s : %s', url, e.args[0])
        raise
    
    alarms = json.loads(r.text)
    if alarms[0] == 'nil':
        logger.error('no alarms time found in google calendar!')
    else:
        conn = None

        try:
            # First empty the table
            conn = sqlite.connect(myprivateinfo['database'])
            c = conn.cursor()
            c.execute('DELETE FROM alarms')
            conn.commit()
            
            c.executemany('INSERT INTO alarms (alarm) VALUES (?)', 
                ((s,) for s in alarms))
            conn.commit()

            conn.close()
        except sqlite.Error, e:
            logger.error('Problem with request INSERT INTO alarms : %s', e.args[0])

        finally:
            if conn:
                conn.close()   

def check_alarm():
    logger.info('check_alarm: time to chime?')
    conn = None

    now = datetime.datetime.now(tzlocal())
    delta = datetime.timedelta(minutes=1)

    try:
        conn = sqlite.connect(myprivateinfo['database'])
        c = conn.cursor()
        c.execute("SELECT * FROM alarms")
        for alarm in c.fetchall():
            logger.debug('alarms data %s', alarm[0])   
            eventDate = dateutil.parser.parse(alarm[0])
            # Date are formatted as ISO8601
            if now >= eventDate:
                # Ok, now if it's too late, forget about it!
                if now - delta < eventDate:
                    logger.info('DRING DRING DRING')
                    q_music.put(Message.alarm)

        conn.close()
    except sqlite.Error, e:
        logger.error('Problem with request SELECT * FROM ALARMS : %s', url, e.args[0])

    finally:
        if conn:
            conn.close()   

def timed_func(flag,cycle,wait,func):
    
    flag -= 1

    if flag == 0:
        flag = cycle / wait
        func()

    return flag

def main():
    # **************************************************************************
    # Main Loop

    # need global so any process can talk to it
    global q_display, q_led, q_music, q_sock, q_sensor
    q_display = Queue()
    q_led = Queue()
    q_music = Queue()
    q_sock = Queue()
    q_sensor = Queue()

    initIO()

    # Ok childrens, time to work!
    Process(target=show_nixie, args=(q_display,)).start()
    Process(target=show_led, args=(q_led,)).start()
    Process(target=play_music, args=(q_music,)).start()
    Process(target=lauch_server, args=(q_sock,)).start()
    Process(target=sensor_detect, args=(q_sensor,)).start()

    logger.info('main started!')
     
    flag_getalarms = REFRESH_ALARMS / CYCLE_MAIN_WAIT
    flag_checkalarms = CHECK_ALARM / CYCLE_MAIN_WAIT

    refresh_alarms()

    while True:
        # Master loop that manage event around here
        #
        # Clock can be in 3 'basic' state:
        #   ************************************************************
        #   * State         | Nixie status | Led status | Sound status *
        #   ************************************************************
        #   * sleeping      |     off      |     off    |      off     *
        #   * alarm         |     on       |     on     |      on      *
        #   * snooze        |     off      |     on     |      pause   *
        #   ************************************************************
        #
        #   There's also 2 externals events that can interac with those state 
        #   which is microphone (noise) and touch (capacitive sensor).
        #   
        #   Also, in specific period (ex. 22h00 to 6h00), those external event 
        #   will have different result. For example, a microphone event in this
        #   period will not turn-on nixie but a touch event always will.
        #
        
        # Get a list of the next 10 alarms every hour
        flag_getalarms = timed_func(flag_getalarms,REFRESH_ALARMS,
            CYCLE_MAIN_WAIT,refresh_alarms)

        # Time to chime the bell?
        flag_checkalarms = timed_func(flag_checkalarms,CHECK_ALARM,
            CYCLE_MAIN_WAIT,check_alarm)

        # Update display with time
        q_display.put(time.strftime('%H%M'))
        
        # Finally...get some rest :-)
        time.sleep(CYCLE_MAIN_WAIT)

# Invoke this daemon!
context = daemon.DaemonContext(
    working_directory=WORKING_DIR,
    umask=0o002,
    pidfile=PIDLockFile(PIDFILE, timeout=2),
    stdout=sys.stdout, # ok for dev phase, otherwise null
    stderr=sys.stderr, # ok for dev phase, otherwise null
    files_preserve=[fh.stream,]
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
