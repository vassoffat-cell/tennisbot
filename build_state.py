"""Пересборка состояния игроков и весов модели. Запускать после обновления файлов в папке data."""
import state

if __name__ == '__main__':
    for tour in ('wta', 'atp'):
        state.save(state.build(tour))
    print('Готово: состояние сохранено в папку state.')
