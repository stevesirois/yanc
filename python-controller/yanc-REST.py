import datetime
import os
import httplib2
import json
import oauth2client
import sqlite3 as sqlite
import socket
import sys

from flask import Flask, request, Response
from oauth2client.client import SignedJwtAssertionCredentials
from apiclient import discovery

# Load private info into JSON Object - SHOULD BE IN SAME DIR AS THIS FILE
with open(os.path.join(os.path.dirname(os.path.realpath(__file__)),
        'private_info.json')) as data_file:
    myprivateinfo = json.load(data_file)

# ******************************************************************************
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
        
        conn = None

        try:
            # Persistant data store setup
            conn = sqlite.connect(myprivateinfo['database'])
            c = conn.cursor()
            # Make shure security on database file AND IT'S FOLDER are writable
            #   be the accound running the server (ex.: www-data).
            #   Lost an 1 hour on this one after transfering to uWSGI server.
            #   All you will get is this stupid message : 
            #       "unable to open database file"
            #   BUT, the conn object is ok, only at the execute of the update
            #   this msg appear!!
            c.execute("UPDATE PARAMS SET PVALUE = ? WHERE PNAME='led-brightness'", 
                (data['value'],)) #Carefull, single element tuple need trailing coma
            print >>sys.stderr, 'execute ok'
            conn.commit()
            conn.close()
        except sqlite.Error, e:
            print >>sys.stderr, 'Error in PUT "%s"' % e.args[0]

        finally:
            if conn:
                conn.close()   

        # Speak to yanc!
        # Create a UDS socket
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server_address = '/tmp/uds_socket'

        try:
            sock.connect(server_address)
        except socket.error, msg:
            print >>sys.stderr, msg
            sys.exit(1)

        try:
            # Send data
            message = data['value']
            #print >>sys.stderr, 'sending "%s"' % message
            sock.sendall(message)

            amount_received = 0
            amount_expected = len(message)
            
            while amount_received < amount_expected:
                data = sock.recv(16)
                amount_received += len(data)

        finally:
            #print >>sys.stderr, 'closing socket'
            sock.close()

        return 'OK'  
    else:
        data = []

        conn = None

        try:        
            conn = sqlite.connect(myprivateinfo['database'])
            c = conn.cursor()
            c.execute("SELECT * FROM PARAMS WHERE PNAME='led-brightness'")
            param_exist = c.fetchone()
            if param_exist:
                data.append(param_exist[1])
    
        except sqlite.Error, e:
            print >>sys.stderr, 'Error in GET "%s"' % e.args[0]

        finally:
            if conn:
                conn.close()   

        js = json.JSONEncoder(indent=4, separators=(',', ': ')).encode(data)

        resp = Response(js, status=200, mimetype='application/json')
        return resp
