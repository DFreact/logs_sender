from app.workers.queue import release_publication, reserve_publications


def dispatch_once(factory, settings, publish):
    with factory.begin() as db:
        reservations = reserve_publications(
            db, settings.publication_seconds, settings.work_batch_size
        )
    # Publish AFTER commit: crash here leaves a reservation that times out. Crash
    # after send may duplicate the wake-up, which is harmless to a leased WorkItem.
    for index, (identifier, _token) in enumerate(reservations):
        try:
            publish(str(identifier))
        except Exception:
            with factory.begin() as db:
                for pending_id, pending_token in reservations[index:]:
                    release_publication(db, pending_id, pending_token)
            raise
    return len(reservations)
