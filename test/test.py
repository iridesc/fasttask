from concurrent.futures import ThreadPoolExecutor
from fasttask_manager.manager import Manager


with ThreadPoolExecutor(max_workers=16) as executor:
    executor.map(
        lambda x: Manager(
            "127.0.0.1",
            port="9001",
            task_name="get_circle_area",
            protocol="https",
        ).create_task(
            {
                "r": x,
            }
        ),
        range(1, 20000),
    )


with ThreadPoolExecutor(max_workers=16) as executor:
    executor.map(
        lambda x: Manager(
            "127.0.0.1",
            port="9001",
            task_name="get_hypotenuse",
            protocol="https",
        ).create_task(
            {
                "a": x,
                "b": x + 1,
            }
        ),
        range(1, 20000),
    )
