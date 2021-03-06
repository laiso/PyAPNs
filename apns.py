from datetime import datetime
from socket import socket, AF_INET, SOCK_STREAM, ssl
from struct import pack, unpack

import simplejson

MAX_PAYLOAD_LENGTH = 256

class APNs(object):
    """A class representing an Apple Push Notification service connection"""
    
    def __init__(self, use_sandbox=False, cert_file=None, key_file=None):
        """
        Set use_sandbox to True to use the sandbox (test) APNs servers. 
        Default is False.
        """
        super(APNs, self).__init__()
        self.use_sandbox    = use_sandbox
        self.cert_file  = cert_file
        self.key_file   = key_file
        self._feedback_connection = None
        self._gateway_connection = None
    
    @classmethod
    def byte_string_to_hex(cls, bstr):
        """
        Convenience method for converting a byte string to its hex
        representation
        """
        return ''.join(['%02x' % i for i in unpack('%iB' % len(bstr), bstr)])
    
    @classmethod
    def byte_string_from_hex(cls, hstr):
        """
        Convenience method for converting a byte string from its hex 
        representation
        """
        byte_array = []
        
        # Make sure input string has an even number of hex characters
        # (2 hex chars = 1 byte). Add leading zero if needed.
        if len(hstr) % 2:
            hstr = '0' + hstr
        
        for i in range(0, len(hstr)/2):
            byte_hex = hstr[i*2:i*2+2]
            byte = int(byte_hex, 16)
            byte_array.append(byte)
        return pack('%iB' % len(byte_array), *byte_array)
    
    @classmethod
    def packed_ushort_big_endian(cls, num):
        """
        Returns an unsigned short in packed big-endian (network) form
        """
        return pack('>H', num)
    
    @classmethod
    def unpacked_ushort_big_endian(cls, bytes):
        """
        Returns an unsigned short from a packed big-endian (network) byte 
        array
        """
        return unpack('>H', bytes)[0]
    
    @classmethod
    def packed_uint_big_endian(cls, num):
        """
        Returns an unsigned int in packed big-endian (network) form
        """
        return pack('>I', num)
    
    @classmethod
    def unpacked_uint_big_endian(cls, bytes):
        """
        Returns an unsigned int from a packed big-endian (network) byte array
        """
        return unpack('>I', bytes)[0]
    
    @property
    def feedback_server(self):
        if not self._feedback_connection:
            self._feedback_connection = FeedbackConnection(
                use_sandbox   = self.use_sandbox, 
                cert_file = self.cert_file, 
                key_file  = self.key_file
            )
        return self._feedback_connection
    
    @property
    def gateway_server(self):
        if not self._gateway_connection:
            self._gateway_connection = GatewayConnection(
                use_sandbox   = self.use_sandbox, 
                cert_file = self.cert_file, 
                key_file  = self.key_file
            )
        return self._gateway_connection


class APNsConnection(object):
    """
    A generic connection class for communicating with the APNs
    """
    def __init__(self, cert_file=None, key_file=None):
        super(APNsConnection, self).__init__()
        self.cert_file  = cert_file
        self.key_file   = key_file
        self._socket    = None
        self._ssl       = None
    
    def __del__(self):
        self._disconnect();
    
    def _connect(self):
        # Establish an SSL connection
        self._socket = socket(AF_INET, SOCK_STREAM)
        self._socket.connect((self.server, self.port))
        self._ssl = ssl(self._socket, self.key_file, self.cert_file)
    
    def _disconnect(self):
        if self._socket:
            self._socket.close()
    
    def _connection(self):
        if not self._ssl:
            self._connect()
        return self._ssl
    
    def read(self, n=None):
        return self._connection().read(n)
    
    def write(self, string):
        return self._connection().write(string)


class PayloadAlert(object):
    def __init__(self, body, action_loc_key=None, loc_key=None, 
                 loc_args=None, launch_image=None):
        super(PayloadAlert, self).__init__()
        self.body = body
        self.action_loc_key = action_loc_key
        self.loc_key = loc_key
        self.loc_args = loc_args
        self.launch_image = launch_image

    def dict(self):
        d = { 'body': self.body }
        if self.action_loc_key:
            d['action-loc-key'] = self.action_loc_key
        if self.loc_key:
            d['loc-key'] = self.loc_key
        if self.loc_args:
            d['loc-args'] = self.loc_args
        if self.launch_image:
            d['launch-image'] = self.launch_image
        return d
        
class PayloadTooLargeError(Exception):
    def __init__(self):
        super(PayloadTooLargeError, self).__init__()

class Payload(object):
    """A class representing an APNs message payload"""
    def __init__(self, alert=None, badge=None, sound=None):
        super(Payload, self).__init__()
        self.alert = alert
        self.badge = badge
        self.sound = sound
        self._check_size()
    
    def dict(self):
        """Returns the payload as a regular Python dictionary"""
        d = {}
        if self.alert:
            # Alert can be either a string or a PayloadAlert
            # object
            if isinstance(self.alert, PayloadAlert):
                d['alert'] = self.alert.dict()
            else:
                d['alert'] = self.alert
        if self.sound:
            d['sound'] = self.sound
        if self.badge:
            d['badge'] = int(self.badge)
        
        return { 'aps': d }
    
    def json(self):
        return simplejson.dumps(self.dict(), separators=(',',':'))
    
    def _check_size(self):
        if len(self.json()) > MAX_PAYLOAD_LENGTH:
            raise PayloadTooLargeError()
        
class FeedbackConnection(APNsConnection):
    """
    A class representing a connection to the APNs Feedback server
    """
    def __init__(self, use_sandbox=False, **kwargs):
        super(FeedbackConnection, self).__init__(**kwargs)
        self.server = (
            'feedback.push.apple.com', 
            'feedback.sandbox.push.apple.com')[use_sandbox]
        self.port = 2196
    
    def _chunks(self):
        BUF_SIZE = 4096
        while 1:
            data = self.read(BUF_SIZE)
            yield data
            if not data:
                break
    
    def items(self):
        """
        A generator that yields (token_hex, fail_time) pairs retrieved from 
        the APNs feedback server
        """
        buff = ''
        for chunk in self._chunks():
            buff += chunk
            
            # Quit if there's no more data to read
            if not buff: 
                break
            
            # Sanity check: after a socket read we should always have at least
            # 6 bytes in the buffer
            if len(buff) < 6:
                break
            
            while len(buff) > 6:
                token_length = APNs.unpacked_ushort_big_endian(buff[4:6])
                bytes_to_read = 6 + token_length
                if len(buff) >= bytes_to_read:
                    fail_time_unix = APNs.unpacked_uint_big_endian(buff[0:4])
                    fail_time = datetime.utcfromtimestamp(fail_time_unix)
                    token = APNs.byte_string_to_hex(buff[6:bytes_to_read])
                    
                    yield (token, fail_time)
                                            
                    # Remove data for current token from buffer
                    buff = buff[bytes_to_read:]
                else:
                    # break out of inner while loop - i.e. go and fetch
                    # some more data and append to buffer
                    break

class GatewayConnection(APNsConnection):
    """
    A class that represents a connection to the APNs gateway server
    """
    def __init__(self, use_sandbox=False, **kwargs):
        super(GatewayConnection, self).__init__(**kwargs)
        self.server = (
            'gateway.push.apple.com', 
            'gateway.sandbox.push.apple.com')[use_sandbox]
        self.port = 2195
        
    def _get_notification(self, token_hex, payload):
        """
        Takes a token as a hex string and a payload as a Python dict and sends 
        the notification
        """
        token_bin = APNs.byte_string_from_hex(token_hex)
        token_length_bin = APNs.packed_ushort_big_endian(len(token_bin))
        payload_json = payload.json()
        payload_length_bin = APNs.packed_ushort_big_endian(len(payload_json))
        
        notification = ('\0' + token_length_bin + token_bin
            + payload_length_bin + payload_json)
        
        return notification

    def send_notification(self, token_hex, payload):
        self.write(self._get_notification(token_hex, payload))

