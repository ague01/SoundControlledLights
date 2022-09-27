from multiprocessing import Process
from xml.dom import minidom as md
from music_player import Chunk
from scipy.fftpack import fft
from collections import deque
import numpy as np
import subprocess
import pyaudio
import wave
import sys
import os

'''
NB: - popleft() e append per gestire la lista dei file
    - a fine canzone ci sarà una cella 'EOSong' nei buffer
'''


# Sottoclasse di Process che si occupa della lettura dei file musicali nelle
# cartelle indicate nel file di configurazione.
# Se il file non è wave lo converte;
# Legge i file uno ad uno e carica metadati in un buffer e i dati musicali nell'altro;
# I dati musicali, frequeze e la fft calcolata sono contenuti in un Chunk;
class MusicReader(Process):
    def __init__(self, sound_data, meta_data, events):
        super(MusicReader, self).__init__()
        # buffers condivisi tra processi
        self.sound_data = sound_data
        self.meta_data = meta_data
        self.events = events
        # lettura configurazioni
        try:
            file_folder = os.path.abspath(os.path.dirname(sys.argv[0]))
            config_file = md.parse(file_folder + '\config.xml')
        except FileNotFoundError:
            print('config.xml non presente nella cartella dei sorgenti')
            exit(1)

        # gestione cartelle con file audio
        fol_tags = config_file.getElementsByTagName('MusicFolder')
        if fol_tags.length < 1:
            # se non ci sono cartelle
            print('Nessuna cartella impostata nel file di configurazione!')
            exit(1)
        # path assoluta di questo file
        abs_path = os.path.abspath(os.path.dirname(sys.argv[0]))
        # crea<ione lista contenente tutte le cartelle musicali
        self.folders = []
        # iterazione per ogni tag folder del xml
        for fol_t in fol_tags:
            # creazione coda per gestire le path dei file presenti nella cartella
            files = deque()
            # determinazione path assolute dei files
            # r=root, d=directories, f = files
            for r, d, f in os.walk(abs_path):
                for file in f:
                    # se il file è un file musicale
                    if '.mp3' in file or '.wav' in file:
                        # aggiunta della path del file alla coda che gestisce i file
                        files.append(os.path.join(r, file))
            # append alla lista folders del dict contenente i dati della cartella e path dei file audio presenti
            try:
                self.folders.append({
                    'name': fol_t.attributes['name'].value,  # nome del file
                    'type': fol_t.attributes['type'].value,  # tipo di file contenuti (mp3, wav) (il programma non ne tiene conto)
                    'path': abs_path + fol_t.attributes['path'].value, # path assoluta della cartella
                    'files': files
                })
            except Exception as ex:
                print(ex)

        # acquisizione soglia fft
        trs = config_file.getElementsByTagName('MinThreshold')
        if trs.length < 1:
            # se non c'è
            print('Nessuna soglia trovato nel file di configurazione!')
            exit(1)
        else:
            # se ci sono fattori
            try:
                self.trs_val = int(trs[0].attributes['value'].value)
            except KeyError as ke:
                print('Attributo "' + str(ke).split("'")[1] + '" di un tag Threshold mancante')
                exit(1)
            except ValueError as ve:
                print('Valore "' + str(ve).split("'")[1] + '"di un tag Threshold non convertibile in float')
                exit(1)

    # converte il file mp3 passato in wav con l'uso di un software esterno ffmpeg
    # ritorna la path del file convertito o None se si sono verificati errori
    def _convert(self, path):
        print('Inizio conversione file: ' + path)
        try:
            # creazione path del file convertito
            conv_path = path[:-4] + '_conv.wav'
            # chiamata del comando per convertire il file mp3
            subprocess.call(['ffmpeg', '-loglevel', 'panic', '-i', path, conv_path, '-y'])
            print('Fine conversione file :' + conv_path)
            return conv_path
        except Exception:
            print('Errore nella conversione')
            return None

    # apre il file referenziato, ne legge i metadati e ritorna il descrittore
    def _open(self, path):
        if path is not None:
            print('Inizio apertura file: ' + path)
            try:
                # apertura file referenziato dalla path in lettura
                wf = wave.open(path, 'rb')
                # scrittura di un dict nel buffer contenente i metadati della canzone. Utilizzati dal player
                self.meta_data.append({
                    'chunk_size': 1024,  # wf.getframerate() // 50,   grandezza in frame degli spezzoni da leggere. Equivale a un decimo di secondo
                    'format': pyaudio.get_format_from_width(wf.getsampwidth()),  # formato del file audio
                    'channels': wf.getnchannels(),  # numero di canali
                    'frame_rate': wf.getframerate(),  # numero di campionamenti o frame al secondo
                    'dtype': 'int{0}'.format(wf.getsampwidth() * 8)  # formato dei valori contenuti nel file
                })
                # ritorna il file descriptor, tipo di dati e numero di frame da leggere
                return (wf, self.meta_data[-1]['dtype'], self.meta_data[-1]['chunk_size'])
            except Exception as ex:
                print('Errore ' + str(ex) + ' nell\'apertura del file')
                return None
        # se la path è None
        else:
            return None

    # legge il file musicale dall'inizio alla fine
    def _read(self, data):
        if data is not None:
            print('Inizio lettura del file')
            (wf, dtype, chunk_size) = data
            try:
                while True:
                    self.events['can_read'].wait()
                    raw = wf.readframes(chunk_size)  # lettura musica
                    if len(raw) < 1:  # se il bramo termina
                        print('Lettura brano terminata')
                        break
                    wf_data = np.frombuffer(raw, dtype=dtype)[::2]  # conversione in numpy array
                    fft_arr = fft(np.array(wf_data, dtype=dtype)[:chunk_size])  # calcolo fft
                    fft_arr = np.abs(fft_arr)  # calcolo modulo fft
                    for index, val in enumerate(fft_arr):  # attuazione soglia di azzeramento
                        if val < self.trs_val:
                            fft_arr[index] = 0
                    # append in coda all'array dei dati musicali(frequenze grezze e modulo dell'fft)
                    self.sound_data.append(Chunk(raw, fft_arr))
                    self.events['can_reproduce'].set()  # wakeup riproduzione
                    # se il buffer è pieno
                    if len(self.sound_data) > self.events['MAXLEN']:
                        self.events['can_read'].clear()
            except Exception as ex:
                print('Errore ' + str(ex) + ' nella lettura del file')

    # funzione innescata quando viene chiamato start() sull'istanza nel main
    def run(self):
        print("Run read")
        # per ogni cartella presente nella lista folders
        for folder in self.folders:
            print('Apertura cartella: ' + folder['name'])
            # iterazione tra le path dei files della cartella
            for file in folder['files']:
                # se il file è un mp3
                if '.mp3' in file:
                    # conversione del file con la funzione __convert
                    conv_file = self._convert(file)
                    # lettura del file
                    self._read(self._open(conv_file))
                    # rimozione del file convertito
                    os.remove(conv_file)
                # se il file è un wav
                elif '.wav' in file:
                    # apertura e lettura del file
                    self._read(self._open(file))
                # inserimento nel buffer della keyword di fine canzone
                self.sound_data.append('EOSong')
            # inserimento keyword di fine lettura
            self.sound_data.append('EOPlaylist')
            print('Brani terminati nella cartella ' + folder['name'])


'''
# eliminazione file wav covertiti
items = os.listdir(folder['path'])
for item in items:
    if item.endswith('_conv.wav'):
        os.remove(os.path.join(folder['path'], item))
'''
s
