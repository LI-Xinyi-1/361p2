#!/usr/bin/python

import select
import socket
import string
import sys
import queue
import threading
import time
import re
import copy
import os

ack_poll = []
send_buffer = {}
output_queue = queue.Queue()
timer = None

class rdp_sender: # rdp sender class
    def __init__(self, f):
        self.state = 'close' # close -> syn-sent -> open -> fin-sent -> close
        self.syn = 0
        self.window = 0
        self.f = f
        
    def __send(self): # put things to send to output queue
        if self.state == 'fin-sent':
            self.state = "close"
            return

        if self.state == 'syn':
            dat = self.packet_prepare("SYN", 0, "")
            self.syn += 1

        if self.state == 'open':
            length = min(1024,self.window)
            string = self.f.read(length)

            if string == "":
                self.syn += 1
                dat = self.packet_prepare("FIN",0,"")
                self.state = "fin-sent"
                
            else:
                dat = self.packet_prepare("DAT", length, string)
                self.syn += len(string)
            
        ack_poll.append(self.syn)
        send_buffer[self.syn] = dat.encode()
        output_queue.put(dat.encode())
        
    def open(self): # start sending
        self.state = 'syn'
        self.__send()
        self.state = 'open'
        

    def packet_prepare(self, cmd, length, payload): # put packet into proper format
        string = cmd + "\n"
        string += f"Sequence: {self.syn}\n"
        string += f"Length: {length}\n"
        
        return string + payload + "\r\n\r\n"

    def recv_ack(self,received_ack,received_windowsize): # receive ack
        global timer
        if self.state == 'open':
            if received_ack == ack_poll[0]:
                timer.cancel()
                timer = threading.Timer(0.5,self.timeout)
                self.window = received_windowsize
                self.__send()
                timer.start()
                tmp = ack_poll.pop(0)
                del send_buffer[tmp]

            else:
                for key in send_buffer:
                    output_queue.put(send_buffer[key])

        if self.state == 'fin-sent':
            
            if received_ack == ack_poll[0]:
                self.state = 'close'
    
    def timeout(self): # timeout for resend 
        global timer
        if self.state == 'close':
            
            return
        for key in send_buffer:
            output_queue.put(send_buffer[key])
        timer = threading.Timer(0.5,self.timeout)
        timer.start()

class rdp_receiver: # rdp receiver class
    def __init__(self, f):
        self.state = 'close' # close -> open -> close
        self.ack = 1
        self.window = 2048
        self.f = f

    def get_state(self):
        return self.state

    def __send(self): # send acknowledgement
        if self.state == 'open':
            dat = self.packet_prepare("ACK")
            output_queue.put(dat.encode())


    def packet_prepare(self, cmd): # put packet into proper format
        string = cmd + "\n"
        string += f"Acknowledgment: {self.ack}\n"
        string += f"Window: {self.window}\n"
        string += "\r\n\r\n"
        return string

    def recv_syn(self, cmd): # receive syn
        if self.state == 'close':
            self.state = 'open'
            self.__send()
    
    def recv_dat(self, packet, syn): # receive payloads
        if self.state != 'open' or syn != self.ack:
            return
        self.window -= len(packet)
        self.ack += len(packet)
        self.__send()
        print(packet, file=self.f, end="")
        self.window += len(packet)

    def recv_fin(self, syn): # end of it
        self.ack += 1
        if syn != self.ack:
            return
        self.__send()
    
def packet_process(msg): # split packet
    headers = dict()
    left = msg.find("\n")
    cmd = msg[0:left]
    msg = msg[left+1:]
    payload = None
    while 1:
        left = msg.find("\n")
        msg_tok = msg[0:left].split(": ")
        if msg_tok[0] not in ["Sequence","Acknowledgment","Window","Length"]:
            payload = copy.deepcopy(msg[0:])
            break
        headers[msg_tok[0]] = msg_tok[1]
        msg = msg[left+1:]
    return cmd,headers,payload

def main(ip_address,port_number,read_file_name,write_file_name): # Main function
    # init socket
    sender = rdp_sender(open(read_file_name, mode="r"))
    receiver = rdp_receiver(open(write_file_name, mode="w"))

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setblocking(0)
    s.bind((ip_address, int(port_number)))

    input = [s]
    output = [s]
    request_message = ""
    
    sender.open()
    global timer
    timer = threading.Timer(0.5,sender.timeout)
    timer.start()

    while sender.state != "close": # if finished then close it
        readable, writable, __ = select.select(input, output, input, 2)
        if s in readable: # receiving
            msg = s.recv(2048).decode()
            request_message += msg
            if request_message.endswith("\r\n\r\n") : # End of request from system use \r\n\r\n
                cmd,headers,payload = packet_process(request_message[0:-4])
                request_message = ""
                
                dateAndTime = time.strftime("%a %b %d %H:%M:%S PDT %Y:", time.localtime())
                print(dateAndTime, "Receive;", cmd + ";", "; ".join(f"{key}: {val}" for key, val in headers.items()))
                if "ACK" == cmd:                    
                    sender.recv_ack(int(headers["Acknowledgment"]),int(headers["Window"]))                    
                if "SYN" == cmd:
                    receiver.recv_syn(int(headers["Sequence"]))
                if "DAT" == cmd:
                    receiver.recv_dat(payload, int(headers["Sequence"]))
                if "FIN" == cmd:
                    receiver.recv_fin(int(headers["Sequence"]))

        if s in writable: # sending
            try:
                output_packet = output_queue.get_nowait()
                
            except queue.Empty:
                continue
            else:
                s.sendto(copy.deepcopy(output_packet), ('h2', 8888))
                cmd,headers,payload = packet_process(output_packet.decode()[0:-4])
                dateAndTime = time.strftime("%a %b %d %H:%M:%S PDT %Y:", time.localtime())
                print(dateAndTime, "Send;", cmd + ";", "; ".join(f"{key}: {val}" for key, val in headers.items()))

if __name__ == '__main__':
    ip_address = sys.argv[1]
    port_number = sys.argv[2]
    read_file_name = sys.argv[3]
    write_file_name = sys.argv[4]
    main(ip_address,port_number,read_file_name,write_file_name)
