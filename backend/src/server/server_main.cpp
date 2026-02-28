#include "Config.h"
#include "HttpServer.h"
#include <boost/asio/io_context.hpp>
#include <iostream>
#include <thread>
#include <vector>

int main(int argc, char *argv[]) {
  try {
    unsigned short port = 5050;

    std::cout << "=== BotFC Standalone Boost Sever ===" << std::endl;

    // Load config
    Config::getInstance().load("../config/robot.yaml");
    port = Config::getInstance().getServerPort();

    std::cout << "Listening on 0.0.0.0:" << port << std::endl;

    boost::asio::io_context ioc{1};

    HttpServer server(ioc, port);
    server.start();

    // Run the async IO Thread
    ioc.run();

  } catch (const std::exception &e) {
    std::cerr << "Fatal Error: " << e.what() << std::endl;
    return EXIT_FAILURE;
  }

  return EXIT_SUCCESS;
}
