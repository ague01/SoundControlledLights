from multiprocessing import Process
from dataclasses import dataclass
import numpy as np
import pyaudio


# classe per la gestione dei dati musicali, contiene frequenze grezze e fft
@dataclass
class Chunk():
    # frequenze grezze
    raw: bytes
    # modulo della trasformata di fouriers
    fft: np.array


# Classe che implementa un processo per la riproduzione dei file audio.
# Esso legge i Chunk nel buffer e li esegue nello stream di output audio.
# Ad ogni brano apre lo stream in base ai suoi metadati, alla fine lo chiude.
# I Chunk vengono letti senza eseguire il pop(), lasciando il dato all' UdpSender
# Appena prima di riprodurre autorizza il processo di invio.
class MusicPlayer(Process):
    # inizializzazione oggetto player, vengono passati i buffer per i dati e quello per gli eventi
    def __init__(self, sound_data, meta_data, events):
        super(MusicPlayer, self).__init__()
        # buffers condivisi tra processi
        self.sound_data = sound_data
        self.meta_data = meta_data
        self.events = events

    # legge i metadati nel buffer -> apre e ritorna uno stream di output audio
    def _open_stream(self):
        try:
            # lettura metadati della canzone
            meta = self.meta_data.pop(0)
            # creazione stream di output audio
            return pyaudio.PyAudio().open(
                format=meta['format'],
                channels=meta['channels'],
                rate=meta['frame_rate'],
                output=True,
            )
        except Exception as ex:
            print('Errore: ' + str(ex) + ' durante apertura stream')
            exit(0)
            return None

    # riproduce un solo brano
    def _play(self, out_stream):
        if out_stream is not None:
            print('Riproduzione canzone iniziata')
            # iterazione all'interno del brano
            while True:
                try:
                    # se è terminato il brano
                    if str(self.sound_data[0]) == 'EOSong':
                        # chiusura dello stream
                        out_stream.close()
                        # wakeup sender
                        self.events['can_send'].set()
                        # se è finita la playlist
                        if str(self.sound_data[1]) == 'EOPlaylist':
                            print('Playlist Terminata')
                            # terminazione processo
                            exit(0)
                        print('Canzone terminata')
                        break
                    # se continua la normale esecuzione e la prima cella del buffer è di tipo chunk
                    elif isinstance(self.sound_data[0], Chunk):
                        # lettura dati 'grezzi' di un chunk musicale dal buffer
                        raw = self.sound_data[0].raw
                        # wakeup sender
                        self.events['can_send'].set()
                        # riproduzione musica
                        out_stream.write(raw)
                except Exception as ex:
                    print('Errore: ' + str(ex) + 'in __play')

    def run(self):
        print("Run music_player")
        # scorre nella playlist
        while True:
            # attende il wakeup
            self.events['can_reproduce'].wait()
            try:
                # riproduzione di un brano
                self._play(self._open_stream())
            except Exception as ex:
                print('Errore run player: ' + ex.__repr__())
                break
        print('Riproduzione generale terminata')
