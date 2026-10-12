import logging


def configurar_logging(nivel=logging.INFO):
    logging.basicConfig(level=nivel, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    return logging.getLogger('nicabot')
