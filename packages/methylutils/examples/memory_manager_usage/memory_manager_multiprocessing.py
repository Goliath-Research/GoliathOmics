import multiprocessing as mp
from methyl_utils import get_memory_manager

def worker(shm_name, shape, dtype):
    manager = get_memory_manager()
    # Child process attaches to shared memory
    array = manager.attach_shared_memory_array(shm_name, shape, dtype)
    
    # Process data...
    array[:] = array * 2
    
    # Cleanup
    manager.cleanup_shared_memory_array(array)

# Parent process
manager = get_memory_manager()
shared_array = manager.create_shared_memory_array((1000,), 'float64')
shm_name = shared_array._shm_name

# Start worker process
p = mp.Process(target=worker, args=(shm_name, (1000,), 'float64'))
p.start()
p.join()