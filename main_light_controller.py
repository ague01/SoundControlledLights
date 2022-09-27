from multiprocessing import Manager, log_to_stderr, get_logger
import logging
import music_reader
import music_player
import udp_sender

'''
interazione con manager.list
push() -> append(value)
pop()  -> pop(0)
'''

if __name__ == '__main__':
    # setup per il logger della componente multiprocessing
    log_to_stderr()
    logger = get_logger()
    logger.setLevel(logging.INFO)
    # setup manager per la gestione dei buffer e eventi condivisi tra processi
    with Manager() as manager:
        # buffer contenente Chunk (Dati musicali: frequenze grezze e fft)
        sound_data = manager.list()
        # buffer contenente dict dei metadati delle canzoni
        meta_data = manager.list()
        # dict contenente gli eventi per la sincronizzazione dei processi e dimensione massima del buffer
        events = {
            'can_reproduce': manager.Event(),
            'can_send': manager.Event(),
            'can_read': manager.Event(),
            'MAXLEN': 100
        }
        # inizializzazione degli eventi
        events['can_reproduce'].clear()
        events['can_send'].clear()
        events['can_read'].set()

        # creazione istanze delle classi necessarie a sincronizzare luci e musica
        re = music_reader.MusicReader(sound_data, meta_data, events)
        pl = music_player.MusicPlayer(sound_data, meta_data, events)
        se = udp_sender.UdpSender(sound_data, meta_data, events)

        # start dei processi
        re.start()
        pl.start()
        se.start()

        # join (attesa da parte del main che i processi abbiano terminato il loro compito)
        re.join()
        pl.join()
        se.join()
