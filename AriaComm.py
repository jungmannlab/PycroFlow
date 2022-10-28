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