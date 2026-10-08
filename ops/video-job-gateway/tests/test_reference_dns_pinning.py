"""Connecting reference media must use the validated IP, retaining TLS SNI."""
import pathlib
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from reference_contract import ReferenceContractError, ReferenceMediaVerifier, _PinnedHTTPSConnection


class PinnedReferenceTests(unittest.TestCase):
    def test_caller_io_error_propagates_without_retrying_another_address(self):
        response = Mock(status=200)
        first = Mock()
        first.getresponse.return_value = response
        second = Mock()
        second.getresponse.return_value = Mock(status=200)
        verifier = ReferenceMediaVerifier(('media.example',))
        original_error = FileNotFoundError('media decoder unavailable')
        with patch('reference_contract._PinnedHTTPSConnection', side_effect=[first, second]) as connect:
            with self.assertRaises(OSError) as caught:
                with verifier._open_pinned('https://media.example/image.png', 'media.example', ('8.8.8.8', '1.1.1.1'), {}, 'video_image'):
                    raise original_error
        self.assertIs(caught.exception, original_error)
        self.assertEqual(connect.call_count, 1)
        response.close.assert_called_once_with()
        first.close.assert_called_once_with()
        second.request.assert_not_called()

    def test_connection_error_retries_the_next_validated_address(self):
        first = Mock()
        first.request.side_effect = OSError('connection unavailable')
        second = Mock()
        response = Mock(status=200)
        second.getresponse.return_value = response
        verifier = ReferenceMediaVerifier(('media.example',))
        with patch('reference_contract._PinnedHTTPSConnection', side_effect=[first, second]) as connect:
            with verifier._open_pinned('https://media.example/image.png', 'media.example', ('8.8.8.8', '1.1.1.1'), {}, 'video_image') as received:
                self.assertIs(received, response)
        self.assertEqual(connect.call_count, 2)
        first.getresponse.assert_not_called()
        first.close.assert_called_once_with()
        response.close.assert_called_once_with()
        second.close.assert_called_once_with()

    def test_unsuccessful_response_is_rejected_and_closed_without_retry(self):
        response = Mock(status=302)
        first = Mock()
        first.getresponse.return_value = response
        verifier = ReferenceMediaVerifier(('media.example',))
        with patch('reference_contract._PinnedHTTPSConnection', return_value=first) as connect:
            with self.assertRaises(ReferenceContractError) as caught:
                with verifier._open_pinned('https://media.example/image.png', 'media.example', ('8.8.8.8', '1.1.1.1'), {}, 'video_image'):
                    self.fail('an unsuccessful response must not be yielded')
        self.assertEqual(caught.exception.code, 'video_image_probe_failed')
        self.assertEqual(connect.call_count, 1)
        response.close.assert_called_once_with()
        first.close.assert_called_once_with()

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
