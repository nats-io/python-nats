import tornado.gen
import time
import datetime

# Protocol
INFO_OP         = b'INFO'
CONNECT_OP      = b'CONNECT'
PUB_OP          = b'PUB'
MSG_OP          = b'MSG'
SUB_OP          = b'SUB'
UNSUB_OP        = b'UNSUB'
PING_OP         = b'PING'
PONG_OP         = b'PONG'
_CRLF_          = b'\r\n'
_SPC_           = b' '
OK_OP           = b'+OK'
ERR_OP          = b'-ERR'
OK              = OK_OP + _CRLF_
PING            = PING_OP + _CRLF_
PONG            = PONG_OP + _CRLF_
CRLF_SIZE       = len(_CRLF_)
MSG_END         = b'\n'

# States
AWAITING_CONTROL_LINE   = 1
AWAITING_MSG_ARG        = 2
AWAITING_MSG_PAYLOAD    = 3
AWAITING_MSG_END        = 4
AWAITING_MINUS_ERR_ARG  = 5

SCRATCH_SIZE = 512
MAX_CONTROL_LINE_SIZE = 1024

class Msg(object):

  def __init__(self, **kwargs):
    self.subject = kwargs["subject"]
    self.reply   = kwargs["reply"]
    self.data    = kwargs["data"]
    self.sid     = kwargs["sid"]

class Parser(object):

  def __init__(self, nc=None):
    self.nc = nc
    self.scratch = ""
    self.state = AWAITING_CONTROL_LINE
    self.needed = 0
    self.msg_arg = {}

  def read(self, io):
    """
    Read loop for gathering bytes from the server in a buffer
    of maximum SCRATCH_SIZE, then received bytes are streamed
    to the parsing callback for processing.
    """
    io.read_bytes(SCRATCH_SIZE, callback=self.parse, streaming_callback=self.parse, partial=True)

  def parse(self, data=''):
    """
    Parses the wire protocol from NATS for the client
    and dispatches the subscription callbacks.
    """
    for c in data:
      self.scratch += c
      if self.state == AWAITING_CONTROL_LINE:

        # MSG
        if self.scratch.startswith(MSG_OP):
          self.state = AWAITING_MSG_ARG

        # OK
        elif self.scratch.startswith(OK):
          # No op. But still consume OK from buffer and parse rest of buffer.
          if len(self.scratch) > len(OK):
            self.scratch = self.scratch[len(OK):]
          else:
            self.scratch = b''
            self.state = AWAITING_CONTROL_LINE


        # -ERR
        elif self.scratch.startswith(ERR_OP):
          self.state = AWAITING_MINUS_ERR_ARG


        # PONG
        elif self.scratch.startswith(PONG):
          self.nc._process_pong()
          if len(self.scratch) > len(PONG):
            self.scratch = self.scratch[len(PONG):]
          else:
            self.scratch = b''
            self.state = AWAITING_CONTROL_LINE


        # PING
        elif self.scratch.startswith(PING):
          self.nc.send_command(PONG)
          if len(self.scratch) > len(PING):
            self.scratch = self.scratch[len(PING):]
          else:
            self.scratch = b''
            self.state = AWAITING_CONTROL_LINE


      elif self.state == AWAITING_MSG_ARG:
        i = self.scratch.find(_CRLF_)
        if i > 0:
          line = self.scratch[:i]
          args = line.split(_SPC_)
          self.msg_arg["subject"] = args[1]
          self.msg_arg["sid"] = int(args[2])

          # Check in case of using a queue
          args_size = len(args)
          if args_size == 5:
            self.msg_arg["reply"] = args[3]
            self.needed = int(args[4])
          elif args_size == 4:
            self.msg_arg["reply"] = ""
            self.needed = int(args[3])
          else:
            raise ErrProtocol("Wrong number of arguments in MSG")
          self.scratch = self.scratch[i+CRLF_SIZE:]
          self.state = AWAITING_MSG_PAYLOAD


      elif self.state == AWAITING_MSG_PAYLOAD:
        if len(self.scratch) >= self.needed:
          payload = self.scratch[:self.needed]
          subject = self.msg_arg["subject"]
          sid     = self.msg_arg["sid"]
          reply   = self.msg_arg["reply"]

          # Set next stage already before dispatching to callback
          self.scratch = self.scratch[self.needed:]
          self.state = AWAITING_MSG_END

          msg = Msg(subject=subject, sid=sid, reply=reply, data=payload)
          self.nc._process_msg(msg)


      elif self.state == AWAITING_MSG_END:
        i = self.scratch.find(MSG_END)
        if i > 0:
          self.scratch = self.scratch[i+1:]
          self.state = AWAITING_CONTROL_LINE


      # -ERR 'error'
      elif self.state == AWAITING_MINUS_ERR_ARG:
        i = self.scratch.find(_CRLF_)
        if i > 0:
          line = self.scratch[:i]
          _, err = line.split(_SPC_, 1)          
          self.nc._process_err(err)
          if len(self.scratch) > i+CRLF_SIZE:
            self.scratch = self.scratch[i+CRLF_SIZE:]
          else:
            self.scratch = b''
            self.state = AWAITING_CONTROL_LINE


class ErrProtocol(Exception):
    pass