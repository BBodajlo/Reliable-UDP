import threading
import time
import random
import socket
import select
import argparse
from functools import reduce

# Settings
# Retransmission timeout
RTO = 0.500
# Number of application bytes in one packet
CHUNK_SIZE = 8
# Initial sequence number for sender transmissions
INIT_SEQNO = 5
# dummy ACK number for sender's packets
__ACK_UNUSED = 2345367


# Message class: we use this class to structure our protocol
# message. The fields in our protocol are:
# seq no: the starting sequence number of application bytes
# on this packet
# ack no: the cumulative ACK number of application bytes
# being acknowledged in this ACK
# len: the number of application bytes being transmitted on
# this packet
# msg: the actual application payload on this packet
# The methods `serialize` and `deserialize` allow the
# conversion of a protocol object to bytes transmissible
# through a sendto() system call and the bytes from a
# recvfrom() system call into a protocol structure.
class Msg:
    def __init__(self, seq, ack, msg):
        self.seq = int(seq)
        self.ack = int(ack)
        self.msg = str(msg)
        self.len = len(self.msg)

    def serialize(self):
        ser_repr = (str(self.seq) + ' | ' + str(self.ack) + ' | ' +
                    str(self.len) + ' | ' + self.msg)
        return ser_repr.encode('utf-8')

    def __str__(self):
        repr = "Seq: " + str(self.seq) + '   '
        repr += "ACK: " + str(self.ack) + '   '
        repr += "Len: " + str(self.len) + '   '
        repr += "Msg: " + self.msg.strip()
        return repr

    @staticmethod
    def deserialize(ser_bytes_msg):
        ser_msg = ser_bytes_msg.decode('utf-8')
        parts = ser_msg.split('|')
        if len(parts) >= 4:
            return Msg(int(parts[0]),
                       int(parts[1]),
                       '|'.join(parts[3:])[1:])
        else:
            print("Error in deserializing into Msg object.")
            exit(-1)


### Helper methods.
#### Initialize a UDP socket
def init_socket(receiver_binding):
    try:
        cs = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        print("[S]: Sender socket created")
    except socket.error as err:
        print('socket open error: {} \n'.format(err))
        exit()
    return cs


#### Slurp a file into a single string.
#### Warning: do not use on very large files
def get_filedata(filename):
    print("[S] Transmitting file {}".format(filename))
    f = open(filename, 'r')
    filedata = f.read()
    f.close()
    return filedata


#### Chunk a large string into fixed size chunks.
#### The first chunk is a string with the number of
#### following chunks.
#### `seq_to_msgindex` tracks the index of the packet
#### that will contain a given sequence number as its
#### starting sequence number.
def chunk_data(filedata):
    global CHUNK_SIZE
    global INIT_SEQNO
    messages = [filedata[i:i + CHUNK_SIZE]
                for i in range(0, len(filedata),
                               CHUNK_SIZE)]
    messages = [str(len(filedata))] + messages
    content_len = reduce(lambda x, y: x + len(y),
                         messages, 0)
    seq_to_msgindex = {}
    accumulated = INIT_SEQNO
    for i in range(0, len(messages)):
        seq_to_msgindex[accumulated] = i
        accumulated += len(messages[i])
    return messages, content_len, seq_to_msgindex


#### Parse command line arguments
def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port',
                        type=int,
                        help="receiver port to connect to (default 50007)",
                        default=50007)
    parser.add_argument('--infile',
                        type=str,
                        help="name of input file (default test-input.txt)",
                        default="test-input.txt")
    parser.add_argument('--winsize',
                        type=int,
                        help="Window size to use in pipelined reliability",
                        default=20)
    args = parser.parse_args()
    return vars(args)


############################################
# Main reliable sending loop
def send_reliable(cs, filedata, receiver_binding, win_size):
    global RTO
    global INIT_SEQNO
    global __ACK_UNUSED
    messages, content_len, seq_to_msgindex = chunk_data(filedata)

    win_left_edge = INIT_SEQNO
    win_right_edge = min(win_left_edge + win_size,
                         INIT_SEQNO + content_len)

    # Method to transmit all data between window left and
    # right edges. Typically used for just fresh
    # transmissions (retransmissions use transmit_one()).
    def transmit_entire_window_from(left_edge):
        latest_tx = left_edge
        while latest_tx < win_right_edge:
            assert (latest_tx in seq_to_msgindex)
            index = seq_to_msgindex[latest_tx]
            msg = messages[index]
            if (latest_tx + len(msg) <=
                    win_right_edge):
                m = Msg(latest_tx, __ACK_UNUSED, msg)
                cs.sendto(
                    m.serialize(),
                    receiver_binding)
                print("Transmitted {}".format(str(m)))
                latest_tx += len(msg)
            else:
                break
        # return last seq no that was actually transmitted
        return latest_tx

    # Transmit one packet from the left edge of the
    # window. Used for retransmissions in pipelined
    # reliability, and also for fresh transmissions in
    # stop-and-wait reliability.
    def transmit_one():
        assert (win_left_edge in seq_to_msgindex)
        index = seq_to_msgindex[win_left_edge]
        msg = messages[index]
        m = Msg(win_left_edge, __ACK_UNUSED, msg)
        cs.sendto(m.serialize(), receiver_binding)
        print("Transmitted {}".format(str(m)))
        return win_left_edge + len(msg)

    # TODO: This is where you will make your changes. You
    # will not need to change any other parts of this file.
    while win_left_edge < INIT_SEQNO + content_len:
        # win_left_edge = transmit_one()
        # data_from_receiver, receiver_addr = cs.recvfrom(100)
        # ack_msg = Msg.deserialize(data_from_receiver)
        # print("Received {}".format(str(ack_msg)))

        inputs = [cs]  # Expecting to read data from these two sockets
        outputs = [cs]  # Going to send dns searches to these sockets
        resend = [cs]
        """"
        Inputs are the servers this server wants to contact
        The loop will contact all of the writable servers/inputs
        It will then receive feedback from each server that is sending back data
        """

        dnsMessage = ""
        timeoutTracker = False

        while inputs:

            read, write, errors = select.select(inputs, outputs, inputs,
                                                RTO)  # Will check if the servers are to be read or written to; will poll every second to check again

            # if not outputs and timeoutTracker is False:
            #   timeoutTracker = True
            # elif timeoutTracker is True:
            #   outputs = resend
            #  timeoutTracker = False

            if not outputs:
                outputs = [cs]

            for s in write:  # Will write to each server that is wanting to initially be written to from inputs
                if win_left_edge < INIT_SEQNO + content_len:
                    transmit_one()
                outputs.remove(s)

            for s in read:  # Will read data from the servers it contacted and that are readable
                try: # Didn't receive ack from server
                    data_from_receiver, receiver_addr = cs.recvfrom(100)
                    ack_msg = Msg.deserialize(data_from_receiver)
                    print("Received {}".format(str(ack_msg)))
                    win_left_edge = ack_msg.ack
                    inputs.remove(s)  # Removing server from inputs since this server has received its data
                    if outputs:
                        outputs.remove(s)
                except:
                    print("dropped ack")
                    outputs = [cs]

                # print("Current ack:\n " + str(ack_msg.ack))

                # if ack_msg != "":  # Since there are no overlapping DNS tables, if we get a response, then that DNS query is done
                # break

            for s in errors:  # To catch errors that are determined by the select statement/stops everything
                print("error in select")
                inputs.remove(s)
                outputs.remove(s)
                print("error in select")
                break
            # if len(read) == 0 and len(write) == 0 and len(
            #       errors) == 0:  # Indicating that there has been no response from either DNS server
            #  break  # Will happen when both TS servers don't send a response

    # if dnsMessage == "":  # dnsMessage is only set when a TS server responds
    #    dnsMessage = data.decode() + " - TIMED OUT"


if __name__ == "__main__":
    args = parse_args()
    filedata = get_filedata(args['infile'])
    receiver_binding = ('localhost', args['port'])
    # receiver_binding = ('', args['port'])
    cs = init_socket(receiver_binding)
    send_reliable(cs, filedata, receiver_binding,
                  args['winsize'])
    cs.close()
    print("[S] Sender finished all transmissions.")
