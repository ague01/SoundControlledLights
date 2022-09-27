from socket import socket, AF_INET, SOCK_STREAM, SOCK_DGRAM
from multiprocessing import Process
from xml.dom import minidom as md
from webcolors import rgb_to_hex
import numpy as np
import os
import sys


# Classe che implementa un processo per la gestione delle stringhe da inviare al gateway dmx.
# Quando il processo di riproduzione abilita questo processo, esso esegue l'operazione
# di pop() dal buffer dei dati musicali e con la fft crea la stringa da inviare al gateway.
# Esso tiene conto dei valori di massima e minima luce impostati per ogni singola lampada.
# Dopo ogni invio esso attende il nuovo wakeup dal processo di riproduzione per inviare il
# successivo pacchetto.
class UdpSender(Process):

    def __init__(self, sound_data, meta_data, events):
        super(UdpSender, self).__init__()
        # buffers condivisi tra processi
        self.sound_data = sound_data
        self.meta_data = meta_data
        self.events = events
        # lettura configurazioni
        try:
            file_folder = os.path.abspath(os.path.dirname(sys.argv[0]))
            config_file = md.parse(file_folder + '\\config.xml')
        except FileNotFoundError:
            print('config.xml non presente nella cartella dei sorgenti')
            exit(1)
        # acquisizione gateway
        gw = config_file.getElementsByTagName('Gateway')
        if gw.length < 1:
            # se non ci sono gateway
            print('Nessun gateway trovato nel file di configurazione!')
            exit(1)
        elif gw.length > 1:
            # se ci sono piu tag Gateway
            print('Più di un gateway, verrà utilizzato il: ' + gw[0].attributes['name'].value)
        try:
            self.gname = gw[0].attributes['name'].value  # nome del gateway
            self.address = gw[0].attributes['address'].value  # indirizzo ip del gateway
            self.port = int(gw[0].attributes['port'].value)  # indirizzo di porta
            self.first_chan = int(gw[0].attributes['firstChannel'].value)
            # primo canale del gateway da utilizzare
        except KeyError as ke:
            print('Attributo "' + str(ke).split("'")[1] + '" del tag Gateway mancante')
            exit(1)

        # acquisizione luci configurate nel file
        li_tags = config_file.getElementsByTagName('Light')
        if li_tags.length < 1:
            # se non ci sono luci
            print('Nessuna luce trovata nel file di configurazione!')
            exit(1)
        else:
            # se ci sono luci
            self.lights = []
            # iterazione tra i tag Light
            for l in li_tags:
                try:
                    # inserimento nell'array delle luci
                    # nell'indice corrispondente alla posizione della luce nel file xml
                    # del dict contenente:
                    #   tipo di luce(non usato, si da per scontato che sia RGB)
                    #   luce minima e massima impostate nel file xml proporzionate al valore massimo
                    #   di un canale (255)
                    self.lights.insert(
                        int(l.attributes['position'].value),
                        {
                            'type': l.attributes['type'].value,
                            'min': int(255.0 * float(l.attributes['minlum'].value)),
                            'max': int(255.0 * float(l.attributes['maxlum'].value))
                        }
                    )
                except KeyError as ke:
                    print('Attributo "' + str(ke).split("'")[1] + '" di un tag Lights mancante')
                    exit(1)
                except ValueError as ve:
                    print('Valore "' + str(ve).split("'")[1] + '"tag Lights non conv. in float')
                    exit(1)

        # acquisizione AudioRange
        scale = config_file.getElementsByTagName('Scale')
        if scale.length < 1:
            # se non c'è
            print('Nessun fattore di scala trovato nel file di configurazione!')
            exit(1)
        else:
            # se ci sono fattori
            try:
                self.scale_val = float(scale[0].attributes['value'].value)
            except KeyError as ke:
                print('Attributo "' + str(ke).split("'")[1] + '" di un tag Scale mancante')
                exit(1)
            except ValueError as ve:
                print('Valore "' + str(ve).split("'")[1] + '"tag Scale non convertibile in float')
                exit(1)
        # acquisizione subvalue dal file di configurazione
        sub = config_file.getElementsByTagName('SubValue')
        if sub.length < 1:
            # se non c'è il tag
            print('Nessun SubValue trovato nel file di configurazione!')
            exit(1)
        else:
            # se ci sono fattori
            try:
                self.sub_val = float(sub[0].attributes['value'].value)
            except KeyError as ke:
                print('Attributo "' + str(ke).split("'")[1] + '" di un tag SubValue mancante')
                exit(1)
            except ValueError as ve:
                print('Valore "' + str(ve).split("'")[1] + '"tag SubValue non conv. in float')
                exit(1)

        # acquisizione range colori dal file di configurazione
        self.colors_range = {
            'Red': (0, 0),
            'Green': (0, 0),
            'Blue': (0, 0)
        }
        # per ogni colore
        for col in self.colors_range:
            c_tag = config_file.getElementsByTagName(col)
            if c_tag.length < 1:
                # se non c'è il tag per quel colore
                print('Nessun tag {col} trovato nel file di configurazione!')
                exit(1)
            else:
                # se ci sono fattori
                try:
                    # creazone tuple inizio fine del range di indici dell fft per colore
                    tup = (
                        int(c_tag[0].attributes['start'].value),
                        int(c_tag[0].attributes['finish'].value)
                    )
                    self.colors_range[col] = tup
                except KeyError as ke:
                    print('Attributo "' + str(ke).split("'")[1] + '" di un tag {col} mancante')
                    exit(1)
                except ValueError as ve:
                    print('Valore "' + str(ve).split("'")[1] + '"tag {col} non conv. in float')
                    exit(1)

        # definizione socket con ipv4 e metodo udp
        self.socket = socket(AF_INET, SOCK_DGRAM)
        try:
            # connessione al gateway
            self.socket.connect((self.address, self.port))
        except ConnectionRefusedError:
            print('Connessione al Gateway fallita.')
            exit(1)

        # set parametri gestione messaggi edmx
        # stringa che spegne tutte le luci
        self.alloff = 'EDMX' + '{:03d}'.format(self.first_chan) + '{:03d}'.format(len(self.lights) * 3)
        self.alloff += ('000000' * len(self.lights))
        # varibile contenente l'ultima stringa inviata. Inizializzata a 0
        self.lastcolor = self.alloff
        # variabile che identifica il numero corrispondente all'ordine dei 3 colori rgb
        # esso verrà cambiato ogni tempo t da un timer ancora da implementare
        self.rgb_order = 0

    # crea le tuple rgb in base all'ordine indicato dalla variabile rgb_order
    def rgb_tuple_creator(self, a, b, c):
        # print(self.rgb_order)
        if self.rgb_order == 0:
            return (a, b, c)
        elif self.rgb_order == 1:
            return (b, a, c)
        elif self.rgb_order == 2:
            return (c, b, a)

    # funzione per arrestare la connessione socket e fermare il processo
    def stop(self):
        exit(1)
        self.socket.shutdown()
        self.socket.close()

    # funzione che attenua i valori rgb della luce alla posizione data in base ai
    # valori impostati nel file di configurazione.
    def rgb_normalization(self, light_pos, color):
        (r, g, b) = color
        if r < self.lights[light_pos]['min']:
            r = self.lights[light_pos]['min']
        if g < self.lights[light_pos]['min']:
            g = self.lights[light_pos]['min']
        if b < self.lights[light_pos]['min']:
            b = self.lights[light_pos]['min']

        if r > self.lights[light_pos]['max']:
            r = self.lights[light_pos]['max']
        if g > self.lights[light_pos]['max']:
            g = self.lights[light_pos]['max']
        if b > self.lights[light_pos]['max']:
            b = self.lights[light_pos]['max']

        ret = (r, g, b)
        return ret

    # crea la stringa da inviare dalle tuple di 3 valori rgb passate
    def __EDMXBuilder(self, *ch_colors):
        # prima parte della stringa: EDMX + canale di partenza + numero di canali da scrivere
        string = 'EDMX' + '{:03d}'.format(self.first_chan) + '{:03d}'.format(len(ch_colors) * 3)
        # per ogni tuple rgb passata
        for c in ch_colors:
            # append alla stringa del corrispondente esadecimale del valore rgb normalizzato
            string += str(rgb_to_hex(self.rgb_normalization(ch_colors.index(c), c))).replace('#', '')
        '''togliere commento per utilizzare la funzionalià descritta
        # se la stringa che è stata formata è uguale all'ultima inviata
        if string == self.lastcolor:
            self.lastcolor = self.alloff
            return self.alloff
        else:
            self.lastcolor = string
        '''
        return string

    # converte la fft in una lista di tuple rgb
    def __fft_converter(self, fft):
        # determinazione valori di intensità per ogni colore in base alla fft
        # proporzionata con i valori inseriti nel file di configurazione subval e scale
        # creazipone argomenti da passare alla funzione per creare la stringa edmx
        r = int(np.mean(fft[self.colors_range['Red'][0]:self.colors_range['Red'][1]]) * self.scale_val - self.sub_val)
        g = int(np.mean(fft[self.colors_range['Green'][0]:self.colors_range['Green'][1]]) * self.scale_val - self.sub_val)
        b = int(np.mean(fft[self.colors_range['Blue'][0]:self.colors_range['Blue'][1]]) * self.scale_val - self.sub_val)

        args = (self.rgb_tuple_creator(r, g, b) for i in range(len(self.lights)))

        return self.__EDMXBuilder(*args)

    # invia la stringa tramite socket dopo averla codificata
    def __send(self, message):
        self.socket.sendall(message.encode())

    # funzione innescata quando viene chiamato start() sull'istanza nel main
    def run(self):
        while True:
            self.events['can_send'].wait()  # attesa che questo processo venga autorizzato dal reader
            self.events['can_send'].clear()  # revoca del permesso del permesso di essere eseguito alla sucessiva iteazione
            msg = self.sound_data.pop(0)  # pop() del messaggio da invaire dal buffer dei dati musicali
            self.events['can_read'].set()  # wakeup del processo di lettura, se non è in esecuzione e non ha finito di leggere
            if str(msg) == 'EOSong':  # se la canzone è terminata
                if str(self.sound_data[0]) == 'EOPlaylist':  # se tutte le canzoni sono terminate
                    print('Invio playlist finito')
                    self.__send(self.alloff)  # spegnimento di tutte le luci connesse
                    exit(0)  # terminazione del thread
                print('invio canzone finito')
            # se si deve inviare il pachetto: il dato letto è di tipo Chunk
            else:
                self.__send(self.__fft_converter(msg.fft))
