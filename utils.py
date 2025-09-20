import os

def get_audio_files(directory):
    if not os.path.isdir(directory):
        return []
    return [f for f in os.listdir(directory) if f.lower().endswith(('.wav', '.mp3'))]
