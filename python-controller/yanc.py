#!/usr/bin/env python
# *********************************************************************
# Y.A.N.C. - Yet Another Nixie Clock
#
# Author	: Steve Sirois
# Version	: A
# Date		: 2015-05-26
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
import datetime
import os
import signal
import httplib2
import json
import oauth2client
import sqlite3

from multiprocessing import Process, Queue
from Queue import Empty
from flask import Flask, request, Response
from oauth2client.client import SignedJwtAssertionCredentials
from apiclient import discovery

# Setup IO (PP = Physical Pin, (l) = Denote an active state on low)
# Nixie Board
out_SRCLR = 26  # PP 37 - SCL [SRCLR] (l) pin on 74LS595 - 0 = clear, 1 = enable
out_led = 12    # PP 32 - Ambient led driver - will be PWM driven

# Digital potentiometer
out_up_down = 22    # PP 15(7) - U/D(l) on DS1804
out_inc = 5         # PP 29(5) - INC(h) on DS1804
out_cs_audio = 13   # PP 33(1) - CS(l) on DS1804 for audio level playback
out_cs_micro = 6    # PP 31(3) - CS(l) on DS1804 for microphone sensitivity
# Audio enabled
out_audio = 24      # PP 18(8) - AUDIO ENABLED on SSM2211
# Inputs
in_noise_detect = 17    # PP 11(9) - Output of OpAmp TL084
in_touch_detect = 25    # PP 22(6) - Atmel AT42QT1011 QTouch Capacitive 

GPIO.setmode(GPIO.BCM)

GPIO.setup(out_SRCLR, GPIO.OUT) 
GPIO.setup(out_led, GPIO.OUT)

GPIO.setup(out_up_down, GPIO.OUT) 
GPIO.setup(out_inc, GPIO.OUT) 
GPIO.setup(out_cs_audio, GPIO.OUT)
GPIO.setup(out_audio, GPIO.OUT) 
GPIO.setup(out_cs_micro, GPIO.OUT)

GPIO.setup(in_noise_detect, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(in_touch_detect, GPIO.IN, pull_up_down=GPIO.PUD_UP) 

# Spare pin
# BCM 23, PP 16(10)

# Define call back for external events (Mic + touch)
def my_callback(channel):
	print "falling edge detected on 23"

def my_callback2(channel):
	print "falling edge detected on 24"

#GPIO.add_event_detect(in_noise_detect, GPIO.FALLING, callback=my_callback, bouncetime=300)  
#GPIO.add_event_detect(in_touch_detect, GPIO.FALLING, callback=my_callback2, bouncetime=300)  
#GPIO.wait_for_edge(in_touch_detect, GPIO.RISING)

# TEMP: Turn Led at 50% for now
led = GPIO.PWM(out_led, 60)
led.start(50)

def show_nixie(q_display):
    # Make sure child process ignore ctrl-c for clean stop
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    # Use SPI to 'talk' to the shift register (74LS595)
    # No bit packing here! :-)
    spi = spidev.SpiDev()
    spi.open(0,0) # port 0, device CE0 [BCM 8 / PP 24]
    # Other detail : SCLK [BCM 11 / PP 23], MOSI [BCM 10 / PP 19]    
    spi.xfer2([0x00])

    usleep = lambda x: time.sleep(x/1000000.0)

    rate = 100 # (Hz) under 50, blinking will start to occur!
    cycle = 1000000.0 / rate / 4 # (us) one full cycle per nixie (4)
    blanking = 300 # (us) - prevent ghosting effect
    turnon = 100 # (us) Turn-on time, typical between 10-100
    
    while True:
        try:
            data = q_display.get_nowait()
            # Must not block! :-) check link below for more info
            # stackoverflow.com/feeds/question/31235112
        except Empty:  # queue was empty, better chance next time
            pass

        if data == 'STOP':
            break
        else: 
            tube = 8
            for d in data:
                if tube == 8 and d == "0":  # don't turn on first 0
                    off_hex = "00"
                    on_hex = "00"
                else:
                    off_hex = "0" + d 
                    on_hex = str(tube) + d 

                spi.xfer2([int(off_hex,16)])
                usleep(blanking) # with d between 0-9, one cathode is always on
                spi.xfer2([int(on_hex,16)])
                usleep(cycle-blanking-turnon)

                # Next tube
                tube = tube >> 1

    # Close all
    spi.xfer2([0x00])
    spi.xfer2([0x00])
    spi.close()
    print "show_nixie is done."


# Load private info into JSON Object - SHOULD BE IN SAME DIR AS THIS FILE
with open(os.path.join(os.path.dirname(os.path.realpath(__file__)),
        'private_info.json')) as data_file:
    myprivateinfo = json.load(data_file)

def get_credentials():
    
    service_account_email = myprivateinfo['service_account_email']

    # Open P12 key file containing private key
    """Note for the PI : PKCS12 format is not supported by the PyCrypto library. 
    Need to convert to PEM : 
    => openssl pkcs12 -in xxxxx.p12 -nodes -nocerts > xxxxx.pem
    Password => notasecret
    This is no big deal since the password is knowed from the whole planet! 
    BUT... keep the file safe, it's YOUR private key! :-)
    """
    with open(myprivateinfo['private_key_file']) as f:
        private_key = f.read()

    # Set credentials for the calendar.readonly API scope
    # This is a "two-legged OAuth" (2LO) scenario
    credentials = SignedJwtAssertionCredentials(service_account_email, 
        private_key, 'https://www.googleapis.com/auth/calendar.readonly')
    
    return credentials

def get_next_event():

    credentials = get_credentials()
    http = credentials.authorize(httplib2.Http())
    service = discovery.build('calendar', 'v3', http=http)

    # My Nixie Clock Calendar ID
    calendarId = myprivateinfo['calendarId']

    now = datetime.datetime.utcnow().isoformat() + 'Z' # 'Z' indicates UTC time
    
    eventsResult = service.events().list(
        calendarId=calendarId, timeMin=now, maxResults=10, singleEvents=True,
        orderBy='startTime').execute()
    
    # Only interested in the list of events on the calendar
    events = eventsResult.get('items', [])
    
    # Only get "start" info, that's all I need
    out = []
    if not events:
        out.append('nil')
    for event in events:
        out.append(event['start'].get('dateTime', event['start'].get('date')))
    
    return json.JSONEncoder(indent=4, separators=(',', ': ')).encode(out)

# ******************************************************************************

app = Flask(__name__)
app_name = 'yanc' 
app_version = 'v1.0'

@app.route('/'+app_name+'/api/'+app_version+'/next-alarm')
def next_alarm():
    return get_next_event()

@app.route('/'+app_name+'/api/'+app_version+'/led-brightness', 
    methods=['PUT', 'GET'])
def led_brightness():
    if request.method == 'PUT':
        data = request.json
        # Persistant data store setup
        conn = sqlite3.connect(myprivateinfo['database'])
        c = conn.cursor()
        c.execute("UPDATE PARAMS SET PVALUE = ? WHERE PNAME='led-brightness'", 
            (data['value'],)) #Carefull, single element tuple need trailing coma
        conn.commit()
        conn.close()
        return 'OK'  
    else:
        data = []
        conn = sqlite3.connect(myprivateinfo['database'])
        c = conn.cursor()
        c.execute("SELECT * FROM PARAMS WHERE PNAME='led-brightness'")
        param_exist = c.fetchone()
        if param_exist:
            data.append(param_exist[1])
        conn.close()
        
        js = json.JSONEncoder(indent=4, separators=(',', ': ')).encode(data)

        resp = Response(js, status=200, mimetype='application/json')
        return resp

def web_server():
    app.run(port=5000, debug=True, host='0.0.0.0', use_reloader=False)
    # IMPORTANT : use_reloader not used otherwise it will reload itself
    #   on startup! Check this: stackoverflow.com/feeds/question/25504149

# Main Loop
if __name__ == '__main__':
    q_display = Queue()
    p = Process(target=show_nixie, args=(q_display,)).start()
    w = Process(target=web_server).start()

    print "main started!"
    try:
        while True:
            q_display.put(time.strftime('%H%M'))
            time.sleep(1)

    except KeyboardInterrupt:
        print "\nmain stopped!"
        q_display.put('STOP')
        GPIO.cleanup()
