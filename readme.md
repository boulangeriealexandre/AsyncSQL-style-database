Add the following section to your `README.md`:

```markdown
## AsyncSQL

This project supports asynchronous MariaDB queries through the `AsyncSQL` class.

Asynchronous queries run on worker threads, so database operations do not block the main server thread.

### Requirements

Install the MariaDB development package.

#### Debian/Ubuntu

```bash
sudo apt install libmariadb-dev
```

### CMake configuration

Add the MariaDB client library to your `CMakeLists.txt`:

```cmake
find_package(Threads REQUIRED)

find_path(MARIADB_INCLUDE_DIR
    NAMES mysql.h
    PATH_SUFFIXES mariadb mysql
)

find_library(MARIADB_LIBRARY
    NAMES mariadb mysqlclient
)

if (NOT MARIADB_INCLUDE_DIR OR NOT MARIADB_LIBRARY)
    message(FATAL_ERROR "MariaDB client library not found")
endif()

target_include_directories(your_server_target PRIVATE
    ${MARIADB_INCLUDE_DIR}
)

target_link_libraries(your_server_target PRIVATE
    ${MARIADB_LIBRARY}
    Threads::Threads
)
```

Replace `your_server_target` with the target name defined in your `CMakeLists.txt`.

Also add the AsyncSQL source file to the target:

```cmake
target_sources(your_server_target PRIVATE
    src/async_sql.cpp
)
```

### AsyncSQL class

Create the following files:

```text
include/async_sql.hpp
src/async_sql.cpp
```

#### `include/async_sql.hpp`

```cpp
#pragma once

#include <mysql.h>

#include <condition_variable>
#include <functional>
#include <mutex>
#include <queue>
#include <string>
#include <thread>
#include <vector>

struct SqlResult {
    bool success = false;
    std::string error;

    std::vector<std::vector<std::string>> rows;
    unsigned long long insert_id = 0;
    unsigned long long affected_rows = 0;
};

class AsyncSQL {
public:
    using Callback = std::function<void(SqlResult)>;

    AsyncSQL(
        std::string host,
        unsigned int port,
        std::string user,
        std::string password,
        std::string database,
        std::size_t worker_count = 2
    );

    ~AsyncSQL();

    void start();
    void stop();

    void query(std::string sql, Callback callback);

private:
    struct Job {
        std::string sql;
        Callback callback;
    };

    void worker();

    std::string host_;
    unsigned int port_;
    std::string user_;
    std::string password_;
    std::string database_;
    std::size_t worker_count_;

    bool stopping_ = false;

    std::mutex mutex_;
    std::condition_variable condition_;
    std::queue<Job> jobs_;
    std::vector<std::thread> workers_;
};
```

#### `src/async_sql.cpp`

```cpp
#include "async_sql.hpp"

AsyncSQL::AsyncSQL(
    std::string host,
    unsigned int port,
    std::string user,
    std::string password,
    std::string database,
    std::size_t worker_count
)
    : host_(std::move(host)),
      port_(port),
      user_(std::move(user)),
      password_(std::move(password)),
      database_(std::move(database)),
      worker_count_(worker_count) {
}

AsyncSQL::~AsyncSQL() {
    stop();
}

void AsyncSQL::start() {
    for (std::size_t i = 0; i < worker_count_; ++i) {
        workers_.emplace_back(&AsyncSQL::worker, this);
    }
}

void AsyncSQL::stop() {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        stopping_ = true;
    }

    condition_.notify_all();

    for (auto& worker : workers_) {
        if (worker.joinable()) {
            worker.join();
        }
    }

    workers_.clear();
}

void AsyncSQL::query(std::string sql, Callback callback) {
    {
        std::lock_guard<std::mutex> lock(mutex_);

        if (stopping_) {
            SqlResult result;
            result.error = "AsyncSQL is stopped";
            callback(std::move(result));
            return;
        }

        jobs_.push(Job{
            std::move(sql),
            std::move(callback)
        });
    }

    condition_.notify_one();
}

void AsyncSQL::worker() {
    MYSQL* connection = mysql_init(nullptr);

    if (!connection) {
        return;
    }

    if (!mysql_real_connect(
            connection,
            host_.c_str(),
            user_.c_str(),
            password_.c_str(),
            database_.c_str(),
            port_,
            nullptr,
            0)) {
        mysql_close(connection);
        return;
    }

    while (true) {
        Job job;

        {
            std::unique_lock<std::mutex> lock(mutex_);

            condition_.wait(lock, [this] {
                return stopping_ || !jobs_.empty();
            });

            if (stopping_ && jobs_.empty()) {
                break;
            }

            job = std::move(jobs_.front());
            jobs_.pop();
        }

        SqlResult result;

        if (mysql_query(connection, job.sql.c_str()) != 0) {
            result.error = mysql_error(connection);
            job.callback(std::move(result));
            continue;
        }

        MYSQL_RES* result_set = mysql_store_result(connection);

        if (result_set) {
            MYSQL_ROW row;
            unsigned int field_count = mysql_num_fields(result_set);

            while ((row = mysql_fetch_row(result_set)) != nullptr) {
                std::vector<std::string> values;
                values.reserve(field_count);

                for (unsigned int i = 0; i < field_count; ++i) {
                    values.emplace_back(row[i] ? row[i] : "");
                }

                result.rows.emplace_back(std::move(values));
            }

            mysql_free_result(result_set);
        }

        result.success = true;
        result.insert_id = mysql_insert_id(connection);
        result.affected_rows = mysql_affected_rows(connection);

        job.callback(std::move(result));
    }

    mysql_close(connection);
}
```

### Starting AsyncSQL

Initialize the database when the server starts:

```cpp
#include "async_sql.hpp"

AsyncSQL database(
    "127.0.0.1",
    3306,
    "game_user",
    "game_password",
    "game_database",
    2
);

database.start();
```

Stop the database workers when the server shuts down:

```cpp
database.stop();
```

The final argument controls the number of database worker threads.

### Executing a query

```cpp
database.query(
    "SELECT id, name FROM players",
    [](SqlResult result) {
        if (!result.success) {
            std::cerr << "SQL error: "
                      << result.error
                      << std::endl;
            return;
        }

        for (const auto& row : result.rows) {
            std::cout << "ID: " << row[0]
                      << ", Name: " << row[1]
                      << std::endl;
        }
    }
);
```

For `INSERT`, `UPDATE`, and `DELETE` queries, use `affected_rows` and `insert_id`:

```cpp
database.query(
    "INSERT INTO players (name) VALUES ('Alex')",
    [](SqlResult result) {
        if (!result.success) {
            std::cerr << result.error << std::endl;
            return;
        }

        std::cout << "Inserted ID: "
                  << result.insert_id
                  << std::endl;
    }
);
```

### Lua integration

When exposing AsyncSQL to Lua, callbacks must be executed on the main server thread.

Do not call Lua directly from an AsyncSQL worker thread.

The expected Lua API is:

```lua
db.query(
    "SELECT id, name FROM players",
    function(result)
        if not result.success then
            print("SQL error: " .. result.error)
            return
        end

        for _, row in ipairs(result.rows) do
            print(row[1], row[2])
        end
    end
)
```

The Lua binding should:

1. Read the SQL query from Lua.
2. Store the Lua callback in the Lua registry.
3. Submit the query to `AsyncSQL`.
4. Add the result to the main-thread callback queue.
5. Invoke the Lua callback during the server tick.
6. Remove the callback from the Lua registry.

### Building

```bash
cmake -S . -B build
cmake --build build -j
```

### Important notes

- Use prepared statements when inserting user-provided data.
- Do not share one `MYSQL*` connection between worker threads.
- Do not access Lua from an AsyncSQL worker thread.
- Limit the size of the SQL job queue.
- Store database credentials outside the source code in production.
- Log failed queries and connection errors.
```

You should replace `your_server_target` with the actual CMake target name used by the project.
