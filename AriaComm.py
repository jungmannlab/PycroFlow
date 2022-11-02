"""
MWE of Aria to computer comm via TCPIP
>>> import socket, sys
>>> sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
>>> server_address = ('localhost', 7167)
>>> sock.bind(server_address)
>>> sock.listen(1)
>>> connection, client_address = sock.accept()
>>> connection.sendall(b'OK')
>>> connection.recv(2)
b'OK'
>>> connection.close()
>
"""
import socket


class AriaConnection:
    """Communication connection to Aria via TCPIP
    TODO: Same with nonblocking calls to include timeouts (
        https://docs.python.org/3/howto/sockets.html)

    Args:
        port : int
            the port the server runs on (locally). Must match the
            setting in Aria software
    """
    def __init__(self, port=7167):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_address = ('localhost', port)
        sock.bind(server_address)
        sock.listen(1)  # exactly one client can connect

    def wait_for_aria_conn(self):
        """connect to the Aria client; blocking.
        """
        self.aria_sock, self.aria_address = self.sock.accept()

    def send_trigger(self):
        """Send the trigger codeword
        """
        self.aria_sock.sendall(b'OK')

    def sense_trigger(self):
        """Wait for the trigger codeword. No other messages can be expected
        in this conversation.
        """
        trig = self.aria_sock.recv(2)
        assert trig==b'OK'

    def __del__(self):
        self.aria_sock.shutdown()
        self.aria_sock.close()
