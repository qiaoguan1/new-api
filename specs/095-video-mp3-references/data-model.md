# Data Model

`ReferenceVideo`: role, ephemeral URL, SHA-256, MIME, byte size, six-decimal duration, width, height.

`ReferenceAudio`: role, ephemeral URL, SHA-256, MIME, codec, byte size, six-decimal duration, sample rate, channels.

`StableReferenceIdentity`: ordered metadata above excluding URL. The digest excludes signed query strings and upstream registration IDs.

`V22Gate`: separate booleans for video, audio, and combined inputs. A true switch alone is insufficient; exact capability and price evidence must also be present.

Current release validates candidate metadata but creates no v2.2 job, reservation, or persisted URL.
