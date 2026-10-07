"""Connecting reference media must use the validated IP, retaining TLS SNI."""
import pathlib
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from reference_contract import _PinnedHTTPSConnection


class PinnedReferenceTests(unittest.TestCase):
    def test_socket_target_uses_validated_address_not_fresh_hostname_resolution(self):
        tls = Mock()
        socket = Mock()
        with patch('reference_contract.socket.create_connection', return_value=socket) as connect:
            connection = _PinnedHTTPSConnection('media.example', '8.8.8.8', timeout=5, context=tls)
            connection.connect()
            connect.assert_called_once_with(('8.8.8.8', 443), 5, None)
            tls.wrap_socket.assert_called_once_with(socket, server_hostname='media.example')
            connection.close()


if __name__ == '__main__':
    unittest.main()
