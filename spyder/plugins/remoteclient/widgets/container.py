# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
#
# Licensed under the terms of the MIT License
# (see spyder/__init__.py for details)

"""Remote client container."""

import json

from qtpy.QtCore import Signal

from spyder.api.translations import _
from spyder.api.widgets.main_container import PluginMainContainer
from spyder.plugins.ipythonconsole.utils.kernel_handler import KernelHandler
from spyder.plugins.remoteclient.api import (
    RemoteClientActions,
    RemoteClientMenus,
    RemoteConsolesMenuSections,
)
from spyder.plugins.remoteclient.api.protocol import ConnectionInfo
from spyder.plugins.remoteclient.widgets import AuthenticationMethod
from spyder.plugins.remoteclient.widgets.connectiondialog import (
    ConnectionDialog,
)
from spyder.utils.workers import WorkerManager


class RemoteClientContainer(PluginMainContainer):

    sig_start_server_requested = Signal(str)
    """
    This signal is used to request starting a remote server.

    Parameters
    ----------
    id: str
        Id of the server that will be started.
    """

    sig_stop_server_requested = Signal(str)
    """
    This signal is used to request stopping a remote server.

    Parameters
    ----------
    id: str
        Id of the server that will be stopped.
    """

    sig_connection_status_changed = Signal(dict)
    """
    This signal is used to update the status of a given connection.

    Parameters
    ----------
    info: ConnectionInfo
        Dictionary with the necessary info to update the status of a
        connection.
    """

    sig_create_ipyclient_requested = Signal(str)
    """
    This signal is used to request starting an IPython console client for a
    remote server.

    Parameters
    ----------
    id: str
        Id of the server for which a client will be created.
    """

    sig_shutdown_kernel_requested = Signal(str, str)
    """
    This signal is used to request shutting down a kernel.

    Parameters
    ----------
    id: str
        Id of the server for which a kernel shutdown will be requested.
    kernel_id: str
        Id of the kernel which will be shutdown in the server.
    """

    # ---- PluginMainContainer API
    # -------------------------------------------------------------------------
    def setup(self):
        # Widgets
        self.create_action(
            RemoteClientActions.ManageConnections,
            _('Manage remote connections...'),
            icon=self._plugin.get_icon(),
            triggered=self._show_connection_dialog,
        )

        self._remote_consoles_menu = self.create_menu(
            RemoteClientMenus.RemoteConsoles,
            _("New console in remote server")
        )

        # Signals
        self.sig_connection_status_changed.connect(
            self._on_connection_status_changed
        )

        # Worker manager to open ssh tunnels in threads
        self._worker_manager = WorkerManager(max_threads=5)

    def update_actions(self):
        pass

    def on_close(self):
        self._worker_manager.terminate_all()

    # ---- Public API
    # -------------------------------------------------------------------------
    def setup_remote_consoles_submenu(self, render=True):
        """Create the remote consoles submenu in the Consoles app one."""
        self._remote_consoles_menu.clear_actions()

        self.add_item_to_menu(
            self.get_action(RemoteClientActions.ManageConnections),
            menu=self._remote_consoles_menu,
            section=RemoteConsolesMenuSections.ManagerSection
        )

        servers = self.get_conf("servers", default={})
        for config_id in servers:
            auth_method = self.get_conf(f"{config_id}/auth_method")
            name = self.get_conf(f"{config_id}/{auth_method}/name")

            action = self.create_action(
                name=config_id,
                text=f"New console in {name} server",
                icon=self.create_icon('ipython_console'),
                triggered=(
                    lambda checked, config_id=config_id:
                    self.sig_create_ipyclient_requested.emit(config_id)
                ),
                overwrite=True
            )
            self.add_item_to_menu(
                action,
                menu=self._remote_consoles_menu,
                section=RemoteConsolesMenuSections.ConsolesSection
            )

        # This is necessary to reposition the menu correctly when rebuilt
        if render:
            self._remote_consoles_menu.render()

    def on_kernel_started(self, ipyclient, kernel_info):
        """
        Actions to take when a remote kernel was started for an IPython console
        client.
        """
        config_id = ipyclient.server_id

        # Connect client's signals
        ipyclient.kernel_id = kernel_info["id"]
        self._connect_ipyclient_signals(ipyclient)

        # Get authentication method
        auth_method = self.get_conf(f"{config_id}/auth_method")

        # Set hostname in the format expected by KernelHandler
        address = self.get_conf(f"{config_id}/{auth_method}/address")
        username = self.get_conf(f"{config_id}/{auth_method}/username")
        port = self.get_conf(f"{config_id}/{auth_method}/port")
        hostname = f"{username}@{address}:{port}"

        # Get password or keyfile/passphrase
        if auth_method == AuthenticationMethod.Password:
            password = self.get_conf(f"{config_id}/password", secure=True)
            sshkey = None
        elif auth_method == AuthenticationMethod.KeyFile:
            sshkey = self.get_conf(f"{config_id}/{auth_method}/keyfile")
            passpharse = self.get_conf(f"{config_id}/passpharse", secure=True)
            if passpharse:
                password = passpharse
            else:
                password = None
        else:
            # TODO: Handle the ConfigFile method here
            pass

        # Generate local connection file from kernel info
        connection_file = KernelHandler.new_connection_file()
        with open(connection_file, "w") as f:
            json.dump(kernel_info["connection_info"], f)

        # Open the tunnel in a worker to avoid blocking the UI
        worker = self._worker_manager.create_python_worker(
            KernelHandler.tunnel_to_kernel,
            kernel_info["connection_info"],
            hostname,
            sshkey,
            password,
        )

        # Save variables necessary to make the connection in the worker
        worker.ipyclient = ipyclient
        worker.connection_file = connection_file
        worker.hostname = hostname
        worker.sshkey = sshkey
        worker.password = password

        # Start worker
        worker.sig_finished.connect(self._finish_kernel_connection)
        worker.start()

    # ---- Private API
    # -------------------------------------------------------------------------
    def _show_connection_dialog(self):
        connection_dialog = ConnectionDialog(self)

        connection_dialog.sig_start_server_requested.connect(
            self.sig_start_server_requested
        )
        connection_dialog.sig_stop_server_requested.connect(
            self.sig_stop_server_requested
        )
        connection_dialog.sig_connections_changed.connect(
            self.setup_remote_consoles_submenu
        )

        self.sig_connection_status_changed.connect(
            connection_dialog.sig_connection_status_changed
        )

        connection_dialog.show()

    def _on_connection_status_changed(self, info: ConnectionInfo):
        """Handle changes in connection status."""
        host_id = info["id"]
        status = info["status"]
        message = info["message"]

        # We need to save this info so that we can show the current status in
        # the connection dialog when it's closed and opened again.
        self.set_conf(f"{host_id}/status", status)
        self.set_conf(f"{host_id}/status_message", message)

    def _connect_ipyclient_signals(self, ipyclient):
        """
        Connect an IPython console client signals to the corresponding ones
        here, which are necessary for kernel management on the server.
        """
        ipyclient.sig_shutdown_kernel_requested.connect(
            self.sig_shutdown_kernel_requested
        )

    def _finish_kernel_connection(self, worker, output, error):
        """Finish connecting a remote kernel to an IPython console client."""
        # Handle errors
        if error:
            worker.ipyclient.show_kernel_error(str(error))
            return

        # Create KernelHandler
        kernel_handler = KernelHandler.from_connection_file(
            worker.connection_file,
            worker.hostname,
            worker.sshkey,
            worker.password,
            kernel_ports=output,
        )

        # Connect client to the kernel
        worker.ipyclient.connect_kernel(kernel_handler)
